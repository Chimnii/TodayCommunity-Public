from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional


HOT_PHASE = "hot"
BACKFILL_PHASE = "backfill"
VALID_PHASES = (HOT_PHASE, BACKFILL_PHASE)

DEFAULT_TOTAL_SECONDS = 20 * 60
DEFAULT_HOT_SECONDS = 7 * 60


class RuntimeLimitReached(RuntimeError):
    """A normal stop signal when a phase or the whole cycle uses its budget."""

    def __init__(self, *, scope: str, reason: str, phase: str) -> None:
        self.scope = scope
        self.reason = reason
        self.phase = phase
        super().__init__(f"{scope} runtime limit reached in {phase}: {reason}")


class CycleBlocked(RuntimeError):
    """A terminal stop signal shared by every phase in the current cycle."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"crawl cycle blocked: {reason}")


@dataclass(frozen=True)
class RequestSlot:
    """A request attempt reserved immediately before network I/O begins."""

    phase: str
    request_number: int
    phase_request_number: int
    started_at: float


class CycleRuntime:
    """Share timing, request, and block state across one crawl cycle.

    The default deadlines reserve at most seven minutes for the hot scan and
    stop the complete cycle after twenty minutes. Request counts are retained
    only as telemetry and stable fetch-order evidence; wall-clock deadlines
    and polite spacing are the operational limits.

    The orchestrator should call :meth:`acquire_request` immediately before
    every HTTP request. A ``RuntimeLimitReached`` with ``scope == "phase"``
    means it is safe to move from hot scan to backfill. ``scope == "cycle"``
    means no more requests may be started in this cycle. A source block should
    be passed to :meth:`block`, which permanently stops both phases.

    This class is intended for a single, serial orchestrator. The injected
    clock and sleeper make budget behavior deterministic in tests.
    """

    def __init__(
        self,
        *,
        min_request_interval_seconds: float,
        total_seconds: float = DEFAULT_TOTAL_SECONDS,
        hot_seconds: float = DEFAULT_HOT_SECONDS,
        monotonic: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], None]] = None,
    ) -> None:
        if min_request_interval_seconds <= 0:
            raise ValueError("min_request_interval_seconds must be greater than zero")
        if total_seconds <= 0:
            raise ValueError("total_seconds must be greater than zero")
        if hot_seconds <= 0 or hot_seconds > total_seconds:
            raise ValueError(
                "hot_seconds must be greater than zero and no more than total_seconds"
            )

        self.min_request_interval_seconds = float(min_request_interval_seconds)
        self.total_seconds = float(total_seconds)
        self.hot_seconds = float(hot_seconds)

        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or time.sleep
        self._started_at = self._monotonic()
        self._cycle_deadline = self._started_at + self.total_seconds
        self._hot_deadline = self._started_at + self.hot_seconds
        self._last_request_at: Optional[float] = None
        self._request_count = 0
        self._phase_request_counts: Dict[str, int] = {
            HOT_PHASE: 0,
            BACKFILL_PHASE: 0,
        }
        self._blocked_reason: Optional[str] = None

    @property
    def started_at(self) -> float:
        return self._started_at

    @property
    def cycle_deadline(self) -> float:
        return self._cycle_deadline

    @property
    def hot_deadline(self) -> float:
        return self._hot_deadline

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def blocked_reason(self) -> Optional[str]:
        return self._blocked_reason

    @property
    def is_blocked(self) -> bool:
        return self._blocked_reason is not None

    def phase_request_count(self, phase: str) -> int:
        self._validate_phase(phase)
        return self._phase_request_counts[phase]

    def remaining_seconds(self, phase: Optional[str] = None) -> float:
        """Return non-negative wall-clock budget for a phase or the cycle."""

        if phase is not None:
            self._validate_phase(phase)
        deadline = self._deadline_for(phase)
        return max(0.0, deadline - self._monotonic())

    def next_request_delay_seconds(self) -> float:
        """Return the remaining global polite-spacing delay."""

        if self._last_request_at is None:
            return 0.0
        elapsed = self._monotonic() - self._last_request_at
        return max(0.0, self.min_request_interval_seconds - elapsed)

    def acquire_request(self, phase: str) -> RequestSlot:
        """Wait for polite spacing, reserve a request attempt, and return it.

        The first request in a cycle is immediate. Spacing is global across
        phase transitions, so the first backfill request also respects the
        most recent hot-scan request.
        """

        self._validate_phase(phase)
        self._raise_if_blocked()
        self._assert_request_available(phase, self._monotonic())
        self._wait_for_request_spacing(phase)
        self._raise_if_blocked()

        started_at = self._monotonic()
        self._assert_request_available(phase, started_at)
        self._request_count += 1
        self._phase_request_counts[phase] += 1
        self._last_request_at = started_at

        return RequestSlot(
            phase=phase,
            request_number=self._request_count,
            phase_request_number=self._phase_request_counts[phase],
            started_at=started_at,
        )

    def block(self, reason: str) -> None:
        """Mark the entire cycle blocked and immediately abort the caller."""

        cleaned_reason = reason.strip() or "source reported a block"
        if self._blocked_reason is None:
            self._blocked_reason = cleaned_reason
        raise CycleBlocked(self._blocked_reason)

    def complete_request(self) -> None:
        """Start the polite interval after the last response was received."""

        if self._last_request_at is not None:
            self._last_request_at = self._monotonic()

    def raise_if_blocked(self) -> None:
        """Re-raise the cycle-wide block signal, if one has been recorded."""

        self._raise_if_blocked()

    def _wait_for_request_spacing(self, phase: str) -> None:
        delay = self.next_request_delay_seconds()
        if delay <= 0:
            return

        now = self._monotonic()
        deadline = self._deadline_for(phase)
        if now + delay >= deadline:
            self._raise_deadline_limit(phase)
        self._sleep(delay)

    def _assert_request_available(self, phase: str, now: float) -> None:
        if phase == HOT_PHASE and now >= self._hot_deadline:
            raise RuntimeLimitReached(
                scope="phase",
                reason="hot_deadline",
                phase=phase,
            )
        if now >= self._cycle_deadline:
            raise RuntimeLimitReached(
                scope="cycle",
                reason="cycle_deadline",
                phase=phase,
            )

    def _raise_deadline_limit(self, phase: str) -> None:
        if phase == HOT_PHASE and self._hot_deadline <= self._cycle_deadline:
            raise RuntimeLimitReached(
                scope="phase",
                reason="hot_deadline",
                phase=phase,
            )
        raise RuntimeLimitReached(
            scope="cycle",
            reason="cycle_deadline",
            phase=phase,
        )

    def _raise_if_blocked(self) -> None:
        if self._blocked_reason is not None:
            raise CycleBlocked(self._blocked_reason)

    def _deadline_for(self, phase: Optional[str]) -> float:
        if phase == HOT_PHASE:
            return self._hot_deadline
        return self._cycle_deadline

    @staticmethod
    def _validate_phase(phase: str) -> None:
        if phase not in VALID_PHASES:
            choices = ", ".join(VALID_PHASES)
            raise ValueError(f"unknown crawl phase {phase!r}; expected one of: {choices}")
