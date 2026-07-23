from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from crawler.config import get_env, get_required_env, is_truthy
from crawler.coverage import (
    CoverageAbsence,
    CoverageAbsenceRepository,
    CoverageInterval,
    CoverageRepository,
    contains_post_id,
    merge_scanned_interval,
    normalize_effective_coverage,
)
from crawler.d1 import D1Client
from crawler.jobs.scan_new_posts import (
    CrawlBlockedError,
    CrawlSourceError,
    CrawlTimeoutError,
    CrawlTransientError,
    existing_post_lookup_query_count,
    fetch_html,
    post_upsert_query_count,
    record_run,
    update_finalized_posts,
    upsert_posts,
    upsert_source,
    utc_now,
)
from crawler.parsers.dcinside import (
    DcinsideListParser,
    DcinsideNavigationDiagnostics,
    DcinsidePost,
    is_qualifying_post,
    navigation_page_relationships_are_valid,
)
from crawler.runtime import (
    BACKFILL_PHASE,
    HOT_PHASE,
    CycleBlocked,
    CycleRuntime,
    RuntimeLimitReached,
)
from crawler.state import SourceState, get_source_state, save_source_state
from crawler.targets import TargetBoard, get_target


DEFAULT_FINALIZATION_AGE_HOURS = 12.0
DEFAULT_HOT_LOOKBACK_MINUTES = 240.0
DEFAULT_HOT_MAX_SECONDS = 7 * 60.0
DEFAULT_CYCLE_MAX_SECONDS = 20 * 60.0
DEFAULT_MIN_REQUEST_INTERVAL_SECONDS = 10.0
DEFAULT_DEEP_RESERVED_SECONDS = 5 * 60.0
DEFAULT_BLOCK_COOLDOWN_HOURS = 6.0
DEFAULT_TRANSIENT_FETCH_ATTEMPTS = 2
TIMEOUT_STREAK_METADATA_KEY = "consecutive_timeout_cycles"
TIMEOUT_FAILURE_THRESHOLD = 3

CYCLE_MODE_FULL = "full"
CYCLE_MODE_HOT = "hot"
CYCLE_MODE_BACKFILL = "backfill"
VALID_CYCLE_MODES = (
    CYCLE_MODE_FULL,
    CYCLE_MODE_HOT,
    CYCLE_MODE_BACKFILL,
)


@dataclass(frozen=True)
class CycleConfig:
    finalization_age_hours: float = DEFAULT_FINALIZATION_AGE_HOURS
    hot_lookback_minutes: float = DEFAULT_HOT_LOOKBACK_MINUTES
    hot_max_seconds: float = DEFAULT_HOT_MAX_SECONDS
    cycle_max_seconds: float = DEFAULT_CYCLE_MAX_SECONDS
    min_request_interval_seconds: float = DEFAULT_MIN_REQUEST_INTERVAL_SECONDS
    deep_reserved_seconds: float = DEFAULT_DEEP_RESERVED_SECONDS
    block_cooldown_hours: float = DEFAULT_BLOCK_COOLDOWN_HOURS
    transient_fetch_attempts: int = DEFAULT_TRANSIENT_FETCH_ATTEMPTS

    def __post_init__(self) -> None:
        if self.finalization_age_hours <= 0:
            raise ValueError("finalization_age_hours must be positive")
        if self.hot_lookback_minutes <= 0:
            raise ValueError("hot_lookback_minutes must be positive")
        if self.hot_max_seconds <= 0 or self.hot_max_seconds > self.cycle_max_seconds:
            raise ValueError("hot_max_seconds must fit inside cycle_max_seconds")
        if self.min_request_interval_seconds <= 0:
            raise ValueError("min_request_interval_seconds must be positive")
        if not 0 <= self.deep_reserved_seconds < self.cycle_max_seconds:
            raise ValueError(
                "deep_reserved_seconds must be nonnegative and fit inside "
                "cycle_max_seconds"
            )
        if (
            self.deep_reserved_seconds > 0
            and self.hot_max_seconds + self.deep_reserved_seconds
            >= self.cycle_max_seconds
        ):
            raise ValueError(
                "cycle_max_seconds must leave time between the hot and history windows"
            )
        if self.block_cooldown_hours <= 0:
            raise ValueError("block_cooldown_hours must be positive")
        if not 1 <= self.transient_fetch_attempts <= 3:
            raise ValueError("transient_fetch_attempts must be between 1 and 3")


@dataclass
class PageSnapshot:
    page: int
    posts: List[DcinsidePost]
    fetch_order: int = 0
    coverage_ordered: bool = True
    navigation_valid: bool = False
    current_page: Optional[int] = None
    last_page: Optional[int] = None
    can_prove_last_page: bool = False
    pagination_clamped: bool = False

    @property
    def newest_created_at(self) -> datetime:
        return max(parse_datetime(post.created_at) for post in self.posts)

    @property
    def oldest_created_at(self) -> datetime:
        return min(parse_datetime(post.created_at) for post in self.posts)


@dataclass
class PhaseSummary:
    run_type: str
    status: str = "completed"
    scanned_pages: int = 0
    scanned_posts: int = 0
    matched_posts: int = 0
    hot_persisted_posts: Optional[int] = None
    target_complete: bool = False
    committed_intervals: int = 0
    confirmed_absences: int = 0
    stop_reason: str = ""
    oldest_seen_at: str = ""
    pages: List[int] = field(default_factory=list)

    def metadata(self) -> str:
        return json.dumps(
            {
                "target_complete": self.target_complete,
                "committed_intervals": self.committed_intervals,
                "confirmed_absences": self.confirmed_absences,
                "hot_persisted_posts": self.hot_persisted_posts,
                "stop_reason": self.stop_reason,
                "oldest_seen_at": self.oldest_seen_at,
                "pages": self.pages,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


class CoverageChanged(RuntimeError):
    """Abort stale historical target selection after absence invalidation."""

    def __init__(self, page: int, posts: Sequence[DcinsidePost]) -> None:
        super().__init__("Stored absence evidence was contradicted by a fetched page.")
        self.page = page
        self.posts = list(posts)


class CoverageOrderUnsafe(RuntimeError):
    """Stop one coverage lane without discarding collection-safe rows."""

    def __init__(self, snapshot: PageSnapshot) -> None:
        super().__init__(
            f"Page {snapshot.page} is complete but not coverage ordered."
        )
        self.snapshot = snapshot


class BackfillPageClamped(CrawlSourceError):
    """Carry collection-safe last-page evidence without allowing coverage."""

    def __init__(
        self,
        *,
        requested_page: int,
        rendered_page: int,
        board_id: str,
        posts: Sequence[DcinsidePost],
        coverage_ordered: bool,
    ) -> None:
        super().__init__(
            "Backfill pagination could not be safely verified "
            f"(requested_page={requested_page}, rendered_page={rendered_page}, "
            f"board_id={board_id!r}): requested_page_mismatch: source rendered "
            "an earlier page"
        )
        self.requested_page = requested_page
        self.rendered_page = rendered_page
        self.posts = list(posts)
        self.coverage_ordered = bool(coverage_ordered)


class CrawlCycle:
    def __init__(
        self,
        *,
        target: TargetBoard,
        config: CycleConfig,
        runtime: CycleRuntime,
        client: Optional[D1Client] = None,
        fetcher: Callable[[str, float], str] = fetch_html,
        cycle_started_at: Optional[datetime] = None,
        mode: str = CYCLE_MODE_FULL,
    ) -> None:
        if mode not in VALID_CYCLE_MODES:
            choices = ", ".join(VALID_CYCLE_MODES)
            raise ValueError(f"unknown crawl mode {mode!r}; expected one of: {choices}")
        if mode != CYCLE_MODE_HOT and config.deep_reserved_seconds <= 0:
            raise ValueError(
                "full and backfill crawl modes require a positive history reservation"
            )
        self.target = target
        self.config = config
        self.runtime = runtime
        self.client = client
        self.fetcher = fetcher
        self.mode = mode
        self.cycle_started_at = ensure_aware(
            cycle_started_at or datetime.now(timezone.utc)
        ).replace(microsecond=0)
        self.run_started_at = self.cycle_started_at.astimezone(timezone.utc).isoformat()
        self.finalization_cutoff = self.cycle_started_at - timedelta(
            hours=config.finalization_age_hours
        )
        self.hot_cutoff = self.cycle_started_at - timedelta(
            minutes=config.hot_lookback_minutes
        )
        self.coverage_repository = CoverageRepository(client) if client else None
        self.absence_repository = (
            CoverageAbsenceRepository(client) if client else None
        )
        self.coverage: List[CoverageInterval] = []
        self.coverage_absences: List[CoverageAbsence] = []
        self.pending_absence: Optional[CoverageAbsence] = None
        self.absence_recheck_page: Optional[int] = None
        self.source_state = SourceState(source_key=target.key)
        self.summaries: List[PhaseSummary] = []
        self.persistence_warnings: List[str] = []
        self._historical_phase_active = False
        self.target_board_id = board_id_from_url(target.board_url)
        # Historical exhaustion needs fresh page-1 end-navigation evidence
        # plus proof from the fetched final page. Page 1 can contain notices
        # and expose an inflated estimate, so accept a smaller verified final
        # page while rejecting a final page beyond the fresh head estimate.
        self.observed_head_last_page: Optional[int] = None

        if self.client:
            upsert_source(self.client, self.target, self.run_started_at)
            self.source_state = get_source_state(self.client, self.target.key) or self.source_state
            self.coverage = self.coverage_repository.load(self.target.key)
            self.coverage_absences = self.absence_repository.load(self.target.key)
            self._restore_block_cooldown_from_runs()

    def run(self) -> Dict[str, object]:
        blocked_until = parse_optional_datetime(self.source_state.blocked_until)
        if blocked_until and blocked_until > self.cycle_started_at:
            return self._result(
                "cooldown",
                self.summaries,
                f"source cooldown remains active until {blocked_until.isoformat()}",
            )

        try:
            if self.mode in {CYCLE_MODE_FULL, CYCLE_MODE_HOT}:
                self._run_hot_scan()
            if self.mode in {CYCLE_MODE_FULL, CYCLE_MODE_BACKFILL}:
                self._run_backfill()
        except CycleBlocked as exc:
            self._mark_active_phase("blocked", exc.reason)
            self._record_block(exc.reason, self.summaries)
            return self._result("blocked", self.summaries, str(exc))
        except CrawlBlockedError as exc:
            try:
                self.runtime.block(str(exc))
            except CycleBlocked as blocked:
                self._mark_active_phase("blocked", blocked.reason)
                self._record_block(blocked.reason, self.summaries)
                return self._result("blocked", self.summaries, str(blocked))
        except CrawlTimeoutError as exc:
            return self._handle_exhausted_timeout(exc)
        except (CrawlSourceError, RuntimeError) as exc:
            self._mark_active_phase("failed", str(exc))
            self._record_failure(str(exc), self.summaries)
            return self._result("failed", self.summaries, str(exc))
        except Exception as exc:
            self._mark_active_phase("failed", str(exc))
            self._record_failure(str(exc), self.summaries)
            return self._result("failed", self.summaries, str(exc))

        timeout_streak = self._timeout_streak()
        if timeout_streak > 0:
            self.source_state.state_metadata[TIMEOUT_STREAK_METADATA_KEY] = 0

        if self.client:
            try:
                save_source_state(self.client, self.source_state)
            except Exception as exc:
                if timeout_streak > 0:
                    reason = f"Could not reset consecutive timeout state: {exc}"
                    self._mark_active_phase("failed", reason)
                    self._record_failure(reason, self.summaries)
                    return self._result("failed", self.summaries, reason)
                self.persistence_warnings.append(
                    f"Could not save non-authoritative source hints: {exc}"
                )
        overall_status = "partial" if any(item.status == "partial" for item in self.summaries) else "completed"
        return self._result(overall_status, self.summaries)

    def _run_hot_scan(self) -> PhaseSummary:
        summary = PhaseSummary(run_type="hot_scan")
        self.summaries.append(summary)
        deduped: Dict[str, DcinsidePost] = {}
        page = 1

        while True:
            try:
                snapshot = self._fetch_page(page, HOT_PHASE)
            except RuntimeLimitReached as exc:
                if exc.scope == "cycle":
                    raise
                summary.status = "partial"
                summary.stop_reason = exc.reason
                break

            if snapshot.pagination_clamped:
                # DCInside renders its physical final page when a sequential
                # request goes past the end. The real final page was already
                # collected on the preceding request, so do not double-count
                # or persist this duplicate response.
                summary.target_complete = True
                summary.stop_reason = "feed_exhausted"
                break

            self._update_summary(summary, snapshot)
            for post in snapshot.posts:
                if is_qualifying_post(
                    post,
                    self.target.min_upvotes,
                    self.target.min_comments,
                ):
                    deduped[post.external_post_id] = post

            if (
                snapshot.coverage_ordered
                and snapshot.oldest_created_at <= self.hot_cutoff
            ):
                summary.target_complete = True
                summary.stop_reason = "lookback_reached"
                break
            page += 1

        qualifying_posts = list(deduped.values())
        if self.mode == CYCLE_MODE_HOT:
            # A later batch failure should leave the freshest Hot candidates
            # persisted first. Keep the legacy scan order in full cycles.
            qualifying_posts.sort(
                key=lambda post: (
                    parse_datetime(post.created_at),
                    int(post.external_post_id),
                ),
                reverse=True,
            )
        qualifying = [asdict(post) for post in qualifying_posts]
        summary.matched_posts = len(qualifying)
        if self.client:
            # A full cycle must preserve the historical lane that follows Hot.
            # A dedicated Hot run has no later source-request lane to protect:
            # its phase deadline already stopped new fetches, so attempt every
            # idempotent D1 write for rows that were successfully scanned. The
            # workflow timeout remains the outer bound and makes real D1 stalls
            # visible as failures instead of green zero-write partial results.
            if self.mode == CYCLE_MODE_FULL and not self._persistence_fits(
                query_count=post_upsert_query_count(len(qualifying)) + 1,
                reserve_seconds=self.config.deep_reserved_seconds,
            ):
                summary.status = "partial"
                summary.stop_reason = "deep_time_reservation"
                return summary
            on_batch_persisted: Optional[Callable[[int], None]] = None
            if self.mode == CYCLE_MODE_HOT:
                summary.hot_persisted_posts = 0

                def record_hot_persisted(count: int) -> None:
                    assert summary.hot_persisted_posts is not None
                    summary.hot_persisted_posts += count

                on_batch_persisted = record_hot_persisted

            upsert_posts(
                self.client,
                self.target,
                qualifying,
                self.run_started_at,
                on_batch_persisted=on_batch_persisted,
            )
            self._record_summary(summary)
        return summary

    def _run_backfill(self) -> List[PhaseSummary]:
        # Recent finalization leaves the configured wall-clock reservation for
        # history. History then uses whatever time remains before the cycle
        # deadline. Request counts are telemetry, not an execution budget.
        recent = self._run_recent_finalization()
        if recent.stop_reason == "no_finalizable_posts":
            historical = PhaseSummary(
                run_type="backfill_history",
                target_complete=False,
                stop_reason="deferred_no_finalizable_posts",
            )
            self.summaries.append(historical)
        else:
            historical = self._run_historical_backfill()

        if self.client:
            self._record_summary(recent)
            self._record_summary(historical)
        return [recent, historical]

    def _run_recent_finalization(self) -> PhaseSummary:
        summary = PhaseSummary(run_type="finalize_recent")
        self.summaries.append(summary)

        try:
            # A full cycle gets fresh page-1 pagination evidence from Hot. A
            # dedicated Backfill run can start from a saved cutoff hint instead,
            # so probe the head first when the normal cutoff search will not.
            # History exhaustion remains fail-closed without this observation.
            cutoff_page_hint = positive_int(
                self.source_state.state_metadata.get("finalize_page_hint"),
                default=1,
            )
            if (
                self.mode == CYCLE_MODE_BACKFILL
                and self.observed_head_last_page is None
                and cutoff_page_hint != 1
            ):
                self._fetch_backfill_page(
                    1,
                    summary,
                    reserve_seconds=self.config.deep_reserved_seconds,
                    allow_unordered=True,
                )
            snapshot = self._locate_cutoff_page(summary)
        except RuntimeLimitReached as exc:
            summary.status = "partial"
            summary.stop_reason = exc.reason
            return summary
        if snapshot is None:
            if summary.target_complete:
                return summary
            summary.status = "partial"
            summary.stop_reason = summary.stop_reason or "cutoff_not_located"
            return summary

        eligible = self._eligible_posts(snapshot.posts)
        if not eligible:
            summary.status = "partial"
            summary.stop_reason = "cutoff_page_has_no_eligible_posts"
            return summary
        if not finalization_eligibility_is_id_suffix(
            snapshot.posts, self.finalization_cutoff
        ):
            # Concurrent inserts can assign neighboring IDs a few seconds out
            # of timestamp order. Never let one still-live observed post sit
            # inside a finalized ID interval; retry after the boundary moves.
            self.source_state.state_metadata["finalize_page_hint"] = snapshot.page
            summary.status = "partial"
            summary.stop_reason = "cutoff_page_eligibility_not_id_suffix"
            return summary

        preview = interval_from_posts(
            source_key=self.target.key,
            posts=eligible,
            checked_at=self.run_started_at,
        )
        if (
            not interval_is_covered(preview, self.coverage)
            and not self._recent_persistence_fits(eligible)
        ):
            summary.status = "partial"
            summary.stop_reason = "deep_time_reservation"
            return summary

        committed = self._commit_finalized_page(snapshot.posts)
        summary.matched_posts = count_qualifying(eligible, self.target)
        summary.committed_intervals = 1 if committed else 0
        summary.target_complete = True
        summary.stop_reason = "cutoff_page_checked" if committed else "already_covered"
        return summary

    def _run_historical_backfill(self) -> PhaseSummary:
        self._historical_phase_active = True
        try:
            return self._run_historical_backfill_impl()
        except CoverageChanged as exc:
            summary = self.summaries[-1]
            self._update_summary(
                summary,
                PageSnapshot(page=exc.page, posts=exc.posts),
            )
            summary.status = "partial"
            summary.target_complete = False
            summary.stop_reason = "absence_invalidated_reselect"
            self.source_state.state_metadata["history_page_hint"] = exc.page
            self.source_state.state_metadata["history_target_mode"] = "gap"
            return summary
        except CoverageOrderUnsafe as exc:
            summary = self.summaries[-1]
            return self._continue_history_after_unordered_page(
                summary,
                exc.snapshot,
            )
        finally:
            self._historical_phase_active = False

    def _continue_history_after_unordered_page(
        self,
        summary: PhaseSummary,
        first_snapshot: PageSnapshot,
    ) -> PhaseSummary:
        """Use the remaining history window without inventing unsafe coverage.

        An unordered page still contains individually validated collection
        rows. Archive those rows, leave that page as a coverage gap, and scan
        older pages sequentially. Later ordered pages may safely advance their
        own coverage intervals; one bumped row must not discard the rest of
        the cycle's reserved history time.
        """

        summary.status = "partial"
        summary.target_complete = False
        summary.stop_reason = "page_order_not_coverage_safe"
        self.source_state.state_metadata["history_order_unsafe_page"] = (
            first_snapshot.page
        )
        self.source_state.state_metadata["history_target_mode"] = "frontier"

        # Scanning past this page is safe only when authoritative coverage
        # already exists on its newer side. Later ordered pages can then form
        # the older side of an explicit numeric gap. Without that upper
        # boundary, committing only older pages could make a later final-page
        # proof declare history complete while this page remains unchecked.
        first_post_ids = [
            int(post.external_post_id) for post in first_snapshot.posts
        ]
        has_newer_coverage = bool(first_post_ids) and any(
            interval.oldest_post_id > max(first_post_ids)
            for interval in self.coverage
        )
        if not has_newer_coverage:
            summary.matched_posts += self._persist_collection_safe_eligible(
                first_snapshot.posts
            )
            self.source_state.state_metadata["history_page_hint"] = (
                first_snapshot.page
            )
            return summary

        snapshot = first_snapshot
        committed_after_unsafe = False
        while True:
            page = snapshot.page
            eligible = self._eligible_posts(snapshot.posts)
            eligibility_safe = finalization_eligibility_is_id_suffix(
                snapshot.posts,
                self.finalization_cutoff,
            )
            if snapshot.coverage_ordered and eligible and not eligibility_safe:
                self.source_state.state_metadata["history_page_hint"] = page
                summary.stop_reason = "history_page_eligibility_not_id_suffix"
                break
            coverage_safe = (
                snapshot.coverage_ordered
                and bool(eligible)
                and eligibility_safe
            )

            if coverage_safe:
                preview = interval_from_posts(
                    source_key=self.target.key,
                    posts=eligible,
                    checked_at=self.run_started_at,
                )
                page_needs_commit = not interval_is_covered(
                    preview,
                    self.coverage,
                )
                if (
                    page_needs_commit
                    and not self._finalization_persistence_fits(eligible)
                ):
                    self.source_state.state_metadata["history_page_hint"] = page
                    summary.stop_reason = "cycle_persistence_budget"
                    break
                committed = self._commit_finalized_page(snapshot.posts)
                summary.matched_posts += count_qualifying(eligible, self.target)
                summary.committed_intervals += 1 if committed else 0
                committed_after_unsafe = (
                    committed_after_unsafe or committed is not None
                )
                self.source_state.backfill_anchor_post_id = str(
                    min(int(post.external_post_id) for post in eligible)
                )
                self.source_state.backfill_anchor_created_at = min(
                    eligible,
                    key=lambda post: int(post.external_post_id),
                ).created_at
                if self._mark_history_frontier_exhausted(snapshot, preview):
                    self.source_state.state_metadata["history_page_hint"] = page
                    summary.stop_reason = (
                        "history_frontier_exhausted_with_unordered_gap"
                    )
                    self.source_state.state_metadata["history_target_mode"] = "gap"
                    break
            else:
                summary.matched_posts += self._persist_collection_safe_eligible(
                    snapshot.posts
                )

            if snapshot.can_prove_last_page:
                self.source_state.state_metadata["history_page_hint"] = page
                summary.stop_reason = "unordered_final_page_checked"
                break

            next_page = page + 1
            self.source_state.state_metadata["history_page_hint"] = next_page
            try:
                snapshot = self._fetch_backfill_page(
                    next_page,
                    summary,
                    allow_unordered=True,
                )
            except CoverageChanged as exc:
                self._update_summary(
                    summary,
                    PageSnapshot(page=exc.page, posts=exc.posts),
                )
                self.source_state.state_metadata["history_page_hint"] = exc.page
                self.source_state.state_metadata["history_target_mode"] = "gap"
                summary.stop_reason = "absence_invalidated_reselect"
                break
            except RuntimeLimitReached as exc:
                summary.stop_reason = f"unordered_continuation_{exc.reason}"
                break

        if committed_after_unsafe:
            self.source_state.state_metadata["history_target_mode"] = "gap"
        return summary

    def _run_historical_backfill_impl(self) -> PhaseSummary:
        summary = PhaseSummary(run_type="backfill_history")
        self.summaries.append(summary)

        effective_coverage = self._effective_coverage()
        gap_cursor = positive_int(
            self.source_state.state_metadata.get("history_gap_cursor"),
            default=0,
        )
        target_id, target_kind = select_history_target(
            effective_coverage,
            prefer_gap=str(
                self.source_state.state_metadata.get("history_target_mode", "gap")
            ) == "gap",
            after_gap_id=gap_cursor or None,
        )
        frontier_exhausted = is_truthy(
            str(
                self.source_state.state_metadata.get(
                    "history_frontier_exhausted", False
                )
            )
        )
        if effective_coverage and frontier_exhausted and target_kind != "gap":
            # The physical end is already proven. Remaining work may only be
            # authoritative numeric gaps between saved intervals.
            target_id, target_kind = select_history_target(
                effective_coverage,
                prefer_gap=True,
                after_gap_id=gap_cursor or None,
            )
            if target_kind != "gap":
                target_id, target_kind = None, "complete"
        if target_kind == "complete":
            summary.target_complete = True
            summary.stop_reason = "history_complete"
            return summary
        if target_kind == "gap" and target_id is not None:
            # This is only a fairness cursor. It never proves coverage and may
            # safely be lost or stale without causing a skipped interval.
            self.source_state.state_metadata["history_gap_cursor"] = target_id
        default_page = positive_int(
            self.source_state.state_metadata.get("finalize_page_hint"), default=1
        ) + 1
        page_hint = positive_int(
            self.source_state.state_metadata.get("history_page_hint"),
            default=default_page,
        )
        self.pending_absence = None
        self.absence_recheck_page = None

        try:
            if target_id is None:
                snapshot = self._fetch_backfill_page(page_hint, summary)
            else:
                snapshot = self._locate_post_id_page(
                    target_id=target_id,
                    page_hint=page_hint,
                    summary=summary,
                )
        except RuntimeLimitReached as exc:
            summary.status = "partial"
            summary.stop_reason = exc.reason
            return summary
        if snapshot is None:
            summary.status = "partial"
            summary.stop_reason = summary.stop_reason or "history_target_not_located"
            if target_kind == "gap":
                self.source_state.state_metadata["history_target_mode"] = "frontier"
            return summary

        page = snapshot.page
        while True:
            eligible = self._eligible_posts(snapshot.posts)
            if eligible:
                if not finalization_eligibility_is_id_suffix(
                    snapshot.posts, self.finalization_cutoff
                ):
                    self.source_state.state_metadata["history_page_hint"] = page
                    summary.status = "partial"
                    summary.target_complete = False
                    summary.stop_reason = "history_page_eligibility_not_id_suffix"
                    break
                preview = interval_from_posts(
                    source_key=self.target.key,
                    posts=eligible,
                    checked_at=self.run_started_at,
                )
                page_needs_commit = not interval_is_covered(
                    preview, self.coverage
                )
                absence_needs_commit = (
                    self.pending_absence is not None
                    and self.pending_absence.older_page == snapshot.page
                    and not any(
                        item.post_id == self.pending_absence.post_id
                        for item in self.coverage_absences
                    )
                )
                if (
                    (page_needs_commit or absence_needs_commit)
                    and not self._finalization_persistence_fits(
                        eligible,
                        extra_queries=1 if absence_needs_commit else 0,
                    )
                ):
                    # This page is still uncommitted, so keep the hint on it.
                    # Advancing here would turn a timing estimate into a hole.
                    self.source_state.state_metadata["history_page_hint"] = page
                    summary.status = "partial"
                    summary.stop_reason = "cycle_persistence_budget"
                    break
                committed = self._commit_finalized_page(snapshot.posts)
                absence_recorded = self._record_pending_absence(snapshot)
                summary.matched_posts += count_qualifying(eligible, self.target)
                summary.committed_intervals += 1 if committed else 0
                summary.confirmed_absences += 1 if absence_recorded else 0
                summary.target_complete = True
                summary.stop_reason = (
                    f"{target_kind or 'frontier'}_checked"
                    if committed
                    else "already_covered"
                )
                self.source_state.backfill_anchor_post_id = str(
                    min(int(post.external_post_id) for post in eligible)
                )
                self.source_state.backfill_anchor_created_at = min(
                    eligible, key=lambda post: int(post.external_post_id)
                ).created_at
                frontier_was_exhausted = self._mark_history_frontier_exhausted(
                    snapshot, preview
                )
                if absence_recorded:
                    summary.stop_reason = "verified_absence_recorded"
                    break
                if self.absence_recheck_page is not None:
                    self.source_state.state_metadata[
                        "history_page_hint"
                    ] = self.absence_recheck_page
                    summary.target_complete = False
                    summary.status = "partial"
                    summary.stop_reason = "absence_evidence_needs_recheck"
                    break
                if frontier_was_exhausted:
                    self.source_state.state_metadata["history_page_hint"] = page
                    summary.stop_reason = "history_frontier_exhausted"
                    break

            next_page = page + 1
            self.source_state.state_metadata["history_page_hint"] = next_page
            page = next_page
            try:
                snapshot = self._fetch_backfill_page(page, summary)
            except RuntimeLimitReached as exc:
                summary.status = "partial"
                summary.stop_reason = exc.reason
                break

        self.source_state.state_metadata["history_target_mode"] = (
            "gap"
            if self.absence_recheck_page is not None
            else ("frontier" if target_kind == "gap" else "gap")
        )
        return summary

    def _locate_cutoff_page(
        self,
        summary: PhaseSummary,
    ) -> Optional[PageSnapshot]:
        page = positive_int(
            self.source_state.state_metadata.get("finalize_page_hint"), default=1
        )
        newer_bound = 0
        older_bound: Optional[int] = None
        step = 1
        visited: Dict[int, PageSnapshot] = {}

        while True:
            # This hint is non-authoritative. Saving the next probe before the
            # fetch lets a deadline stop resume the search instead of
            # restarting from the old page every cycle.
            self.source_state.state_metadata["finalize_page_hint"] = page
            if page in visited:
                summary.stop_reason = "cutoff_search_stalled"
                self.source_state.state_metadata["finalize_page_hint"] = page
                return None
            try:
                snapshot = self._fetch_backfill_page(
                    page,
                    summary,
                    reserve_seconds=self.config.deep_reserved_seconds,
                    allow_unordered=True,
                )
            except BackfillPageClamped as exc:
                # A board younger than the finalization window has no cutoff
                # page yet. Use the clamped response only as non-authoritative
                # proof that the physical final page is still too new. It must
                # never flow into a coverage commit.
                self.source_state.state_metadata["finalize_page_hint"] = (
                    exc.rendered_page
                )
                if (
                    exc.coverage_ordered
                    and not self._eligible_posts(exc.posts)
                ):
                    summary.target_complete = True
                    summary.stop_reason = "no_finalizable_posts"
                    return None
                raise
            visited[page] = snapshot
            eligible_count = len(self._eligible_posts(snapshot.posts))

            if not snapshot.coverage_ordered and eligible_count > 0:
                summary.matched_posts += self._persist_collection_safe_eligible(
                    snapshot.posts,
                    reserve_seconds=self.config.deep_reserved_seconds,
                )
                summary.stop_reason = "page_order_not_coverage_safe"
                self.source_state.state_metadata["finalize_page_hint"] = page
                return None

            if 0 < eligible_count < len(snapshot.posts):
                self.source_state.state_metadata["finalize_page_hint"] = page
                return snapshot
            if eligible_count == 0:
                newer_bound = max(newer_bound, page)
                if older_bound is not None:
                    if older_bound - newer_bound <= 1:
                        cached_older = visited.get(older_bound)
                        if cached_older is not None:
                            self.source_state.state_metadata[
                                "finalize_page_hint"
                            ] = older_bound
                            return cached_older
                        page = older_bound
                    else:
                        page = (newer_bound + older_bound) // 2
                else:
                    page += step
                    step *= 2
                continue

            older_bound = page if older_bound is None else min(older_bound, page)
            if page == 1 or (newer_bound and older_bound - newer_bound <= 1):
                self.source_state.state_metadata["finalize_page_hint"] = page
                return snapshot
            if newer_bound:
                page = (newer_bound + older_bound) // 2
            else:
                page = max(1, page - step)
                step *= 2

    def _locate_post_id_page(
        self,
        *,
        target_id: int,
        page_hint: int,
        summary: PhaseSummary,
    ) -> Optional[PageSnapshot]:
        page = page_hint
        newer_bound = 0
        older_bound: Optional[int] = None
        step = 1
        visited: Dict[int, PageSnapshot] = {}

        while True:
            self.source_state.state_metadata["history_page_hint"] = page
            if page in visited:
                summary.stop_reason = "history_search_stalled"
                self.source_state.state_metadata["history_page_hint"] = page
                return None
            snapshot = self._fetch_backfill_page(page, summary)
            visited[page] = snapshot
            post_ids = [int(post.external_post_id) for post in snapshot.posts]
            oldest_id = min(post_ids)
            newest_id = max(post_ids)
            if (
                target_id < oldest_id
                and self.observed_head_last_page is not None
                and self.observed_head_last_page >= snapshot.page
                and snapshot.can_prove_last_page
            ):
                # The target is older than every post exposed by the board.
                # Return the verified final page so an already-persisted
                # coverage interval can restore a lost exhaustion marker
                # without probing an out-of-range page.
                self.source_state.state_metadata["history_page_hint"] = page
                summary.stop_reason = "history_target_past_final_page"
                return snapshot
            if oldest_id <= target_id <= newest_id:
                self.source_state.state_metadata["history_page_hint"] = page
                return snapshot

            if oldest_id > target_id:
                newer_bound = max(newer_bound, page)
                if older_bound is not None:
                    if older_bound - newer_bound <= 1:
                        cached_older = visited.get(older_bound)
                        if cached_older is not None:
                            return self._finish_between_pages(
                                target_id=target_id,
                                newer_page=newer_bound,
                                older_page=older_bound,
                                visited=visited,
                                summary=summary,
                            )
                        page = older_bound
                        continue
                    page = (newer_bound + older_bound) // 2
                else:
                    page += step
                    step *= 2
            else:
                older_bound = page if older_bound is None else min(older_bound, page)
                if page == 1:
                    summary.stop_reason = "history_target_newer_than_board"
                    return None
                if newer_bound:
                    if older_bound - newer_bound <= 1:
                        cached_older = visited.get(older_bound)
                        if cached_older is not None:
                            return self._finish_between_pages(
                                target_id=target_id,
                                newer_page=newer_bound,
                                older_page=older_bound,
                                visited=visited,
                                summary=summary,
                            )
                        page = older_bound
                        continue
                    page = (newer_bound + older_bound) // 2
                else:
                    page = max(1, page - step)
                    step *= 2

    def _finish_between_pages(
        self,
        *,
        target_id: int,
        newer_page: int,
        older_page: int,
        visited: Dict[int, PageSnapshot],
        summary: PhaseSummary,
    ) -> PageSnapshot:
        """Return the older page and retain only strong missing-ID evidence."""
        newer_before = visited[newer_page]
        older = visited[older_page]
        newer_after: Optional[PageSnapshot] = None
        if (
            newer_before.fetch_order < older.fetch_order
        ):
            # Re-fetch the leading page after the trailing page. Exact ID
            # stability closes the deletion-shift hole left by a one-way
            # sweep: a live target moving into the first page is then seen.
            # Persist the safe restart point before the request so a deadline
            # cannot leave the next cycle starting in the wrong order.
            self.source_state.state_metadata["history_page_hint"] = newer_page
            newer_after = self._fetch_backfill_page(newer_page, summary)

        self.pending_absence = (
            self._build_absence_evidence(
                target_id=target_id,
                newer_before=newer_before,
                older=older,
                newer_after=newer_after,
            )
            if newer_after is not None
            else None
        )
        self.absence_recheck_page = (
            None if self.pending_absence else newer_page
        )
        # A newer-then-older observation is required because new head posts
        # can otherwise shift an existing boundary row out of both snapshots.
        # Reposition the next attempt when the current order cannot prove it.
        self.source_state.state_metadata["history_page_hint"] = (
            older_page if self.pending_absence else newer_page
        )
        summary.stop_reason = (
            "history_target_between_pages_verified"
            if self.pending_absence
            else "history_target_between_pages_unverified"
        )
        return older

    def _build_absence_evidence(
        self,
        *,
        target_id: int,
        newer_before: PageSnapshot,
        older: PageSnapshot,
        newer_after: PageSnapshot,
    ) -> Optional[CoverageAbsence]:
        if (
            older.page != newer_before.page + 1
            or newer_after.page != newer_before.page
            or not newer_before.navigation_valid
            or not older.navigation_valid
            or not newer_after.navigation_valid
            or newer_before.current_page != newer_before.page
            or older.current_page != older.page
            or newer_after.current_page != newer_after.page
            or newer_before.last_page is None
            or newer_before.last_page != older.last_page
            or newer_before.last_page != newer_after.last_page
            or newer_before.fetch_order <= 0
            or not (
                newer_before.fetch_order
                < older.fetch_order
                < newer_after.fetch_order
            )
        ):
            return None

        before_ids = tuple(
            int(post.external_post_id) for post in newer_before.posts
        )
        after_ids = tuple(
            int(post.external_post_id) for post in newer_after.posts
        )
        if before_ids != after_ids:
            return None

        newer_boundary = min(
            newer_after.posts, key=lambda post: int(post.external_post_id)
        )
        older_boundary = max(
            older.posts, key=lambda post: int(post.external_post_id)
        )
        newer_boundary_id = int(newer_boundary.external_post_id)
        older_boundary_id = int(older_boundary.external_post_id)
        if not older_boundary_id < target_id < newer_boundary_id:
            return None
        if any(
            int(post.external_post_id) == target_id
            for post in (
                *newer_before.posts,
                *older.posts,
                *newer_after.posts,
            )
        ):
            return None
        if (
            parse_datetime(newer_boundary.created_at) > self.finalization_cutoff
            or parse_datetime(older_boundary.created_at) > self.finalization_cutoff
        ):
            return None

        return CoverageAbsence(
            source_key=self.target.key,
            post_id=target_id,
            newer_page=newer_after.page,
            older_page=older.page,
            newer_boundary_post_id=newer_boundary_id,
            older_boundary_post_id=older_boundary_id,
            checked_at=self.run_started_at,
            created_at=self.run_started_at,
            updated_at=self.run_started_at,
        )

    def _fetch_backfill_page(
        self,
        page: int,
        summary: PhaseSummary,
        reserve_seconds: float = 0.0,
        allow_unordered: bool = False,
    ) -> PageSnapshot:
        try:
            snapshot = self._fetch_page(
                page,
                BACKFILL_PHASE,
                reserve_seconds=reserve_seconds,
            )
        except RuntimeLimitReached as exc:
            summary.status = "partial"
            summary.stop_reason = exc.reason
            raise
        self._update_summary(summary, snapshot)
        if not allow_unordered and not snapshot.coverage_ordered:
            raise CoverageOrderUnsafe(snapshot)
        return snapshot

    def _fetch_page(
        self,
        page: int,
        phase: str,
        *,
        reserve_seconds: float = 0.0,
    ) -> PageSnapshot:
        url = self.target.list_url_template.format(page=page)
        html = ""
        request_slot = None
        last_timeout: Optional[CrawlTimeoutError] = None
        all_transient_attempts_timed_out = True
        for attempt in range(1, self.config.transient_fetch_attempts + 1):
            remaining_for_request = (
                self.runtime.remaining_seconds(phase) - max(0.0, reserve_seconds)
            )
            spacing_guard = self.runtime.next_request_delay_seconds()
            if remaining_for_request <= spacing_guard + 1.0:
                if last_timeout is not None and all_transient_attempts_timed_out:
                    raise last_timeout
                raise RuntimeLimitReached(
                    scope="phase" if reserve_seconds > 0 else (
                        "cycle" if phase == BACKFILL_PHASE else "phase"
                    ),
                    reason=(
                        "deep_time_reservation"
                        if reserve_seconds > 0
                        else "request_timeout_budget"
                    ),
                    phase=phase,
                )
            try:
                request_slot = self.runtime.acquire_request(phase)
            except RuntimeLimitReached:
                if last_timeout is not None and all_transient_attempts_timed_out:
                    raise last_timeout
                raise
            remaining_for_request = (
                self.runtime.remaining_seconds(phase) - max(0.0, reserve_seconds)
            )
            if remaining_for_request <= 1.0:
                if last_timeout is not None and all_transient_attempts_timed_out:
                    raise last_timeout
                raise RuntimeLimitReached(
                    scope="phase" if reserve_seconds > 0 else (
                        "cycle" if phase == BACKFILL_PHASE else "phase"
                    ),
                    reason=(
                        "deep_time_reservation"
                        if reserve_seconds > 0
                        else "request_timeout_budget"
                    ),
                    phase=phase,
                )
            timeout_seconds = min(30.0, remaining_for_request)

            try:
                html = self.fetcher(url, timeout_seconds)
                break
            except CrawlBlockedError as exc:
                self.runtime.block(str(exc))
                raise AssertionError("CycleRuntime.block always raises")
            except CrawlTimeoutError as exc:
                last_timeout = exc
                if attempt >= self.config.transient_fetch_attempts:
                    if not all_transient_attempts_timed_out:
                        raise CrawlTransientError(str(exc)) from exc
                    raise
            except CrawlTransientError:
                all_transient_attempts_timed_out = False
                if attempt >= self.config.transient_fetch_attempts:
                    raise
            finally:
                self.runtime.complete_request()

        if request_slot is None:
            raise AssertionError("A page fetch must reserve at least one request slot")

        parser = DcinsideListParser(
            base_url=self.target.board_url,
            now=self.cycle_started_at,
            min_upvotes=self.target.min_upvotes,
            min_comments=self.target.min_comments,
            requested_page=page,
            expected_board_id=self.target_board_id,
        )
        parser.feed(html)
        parser.close()
        if not parser.diagnostics.is_collection_safe:
            details = "; ".join(error.message for error in parser.diagnostics.errors[:3])
            raise CrawlSourceError(
                "Page could not be completely parsed for collection "
                f"(page={page}, candidates={parser.diagnostics.candidate_rows}, "
                f"parsed={parser.diagnostics.parsed_rows}, "
                f"unique_canonical_ids={parser.diagnostics.has_unique_canonical_ids}, "
                f"strictly_descending={parser.diagnostics.ids_strictly_descending}, "
                f"coverage_ordered={parser.diagnostics.ids_coverage_ordered}): {details}"
            )
        navigation = parser.navigation_diagnostics
        pagination_clamped = navigation_is_clamped_to_earlier_page(
            navigation,
            requested_page=page,
        )
        if phase == BACKFILL_PHASE and not navigation.is_valid:
            if pagination_clamped:
                assert navigation.current_page is not None
                raise BackfillPageClamped(
                    requested_page=page,
                    rendered_page=navigation.current_page,
                    board_id=navigation.expected_board_id or self.target_board_id,
                    posts=parser.posts,
                    coverage_ordered=parser.diagnostics.is_coverage_safe,
                )
            details = "; ".join(
                f"{error.code}: {error.message}"
                for error in navigation.errors[:3]
            )
            if not details:
                details = (
                    "pagination container/current-page evidence is missing or ambiguous"
                )
            raise CrawlSourceError(
                "Backfill pagination could not be safely verified "
                f"(requested_page={page}, rendered_page={navigation.current_page}, "
                f"board_id={navigation.expected_board_id!r}): {details}"
            )

        self._invalidate_observed_absences(page, parser.posts)

        if (
            page == 1
            and navigation.is_valid
            and navigation.last_page is not None
        ):
            self.observed_head_last_page = navigation.last_page

        return PageSnapshot(
            page=page,
            posts=parser.posts,
            fetch_order=request_slot.request_number,
            coverage_ordered=parser.diagnostics.is_coverage_safe,
            navigation_valid=navigation.is_valid,
            current_page=navigation.current_page,
            last_page=navigation.last_page,
            can_prove_last_page=navigation.can_prove_last_page,
            pagination_clamped=pagination_clamped,
        )

    def _invalidate_observed_absences(
        self, page: int, posts: Sequence[DcinsidePost]
    ) -> None:
        if not self.coverage_absences or not posts:
            return
        observed_ids = {int(post.external_post_id) for post in posts}
        invalidated = [
            absence
            for absence in self.coverage_absences
            if absence.post_id in observed_ids
        ]
        for absence in invalidated:
            if self.absence_repository:
                self.absence_repository.delete(
                    absence.source_key, absence.post_id
                )
        invalidated_ids = {absence.post_id for absence in invalidated}
        self.coverage_absences = [
            absence
            for absence in self.coverage_absences
            if absence.post_id not in invalidated_ids
        ]
        if invalidated and self._historical_phase_active:
            raise CoverageChanged(page, posts)

    def _eligible_posts(self, posts: Sequence[DcinsidePost]) -> List[DcinsidePost]:
        return [
            post
            for post in posts
            if parse_datetime(post.created_at) <= self.finalization_cutoff
        ]

    def _persist_collection_safe_eligible(
        self,
        posts: Sequence[DcinsidePost],
        *,
        reserve_seconds: float = 0.0,
    ) -> int:
        """Archive qualifying rows without creating authoritative coverage."""

        eligible = self._eligible_posts(posts)
        matched = count_qualifying(eligible, self.target)
        if not self.client or not eligible:
            return matched
        if not self._persistence_fits(
            query_count=(
                existing_post_lookup_query_count(len(eligible))
                + post_upsert_query_count(len(eligible))
            ),
            reserve_seconds=reserve_seconds,
        ):
            return matched
        update_finalized_posts(
            self.client,
            self.target,
            [asdict(post) for post in eligible],
            self.run_started_at,
        )
        return matched

    def _commit_finalized_page(
        self,
        posts: Sequence[DcinsidePost],
    ) -> Optional[CoverageInterval]:
        if not finalization_eligibility_is_id_suffix(
            posts, self.finalization_cutoff
        ):
            raise ValueError(
                "Finalized coverage requires eligible posts to be an ID suffix."
            )
        eligible = self._eligible_posts(posts)
        if not eligible:
            return None

        interval = interval_from_posts(
            source_key=self.target.key,
            posts=eligible,
            checked_at=self.run_started_at,
        )
        if interval_is_covered(interval, self.coverage):
            return None

        if self.client:
            # Archive writes must succeed before coverage advances. Partial
            # duplicate post writes are safe because posts are idempotent.
            update_finalized_posts(
                self.client,
                self.target,
                [asdict(post) for post in eligible],
                self.run_started_at,
            )
        committed = interval

        if self.coverage_repository:
            committed = self.coverage_repository.record_scanned(
                committed,
                existing=self.coverage,
            )
        merge_result = merge_scanned_interval(self.coverage, committed)
        superseded_keys = {item.key for item in merge_result.superseded}
        self.coverage = [
            item for item in self.coverage if item.key not in superseded_keys
        ]
        self.coverage.append(merge_result.merged)
        return merge_result.merged

    def _effective_coverage(self) -> List[CoverageInterval]:
        return normalize_effective_coverage(
            self.coverage,
            self.coverage_absences,
        )

    def _record_pending_absence(
        self,
        snapshot: PageSnapshot,
    ) -> Optional[CoverageAbsence]:
        absence = self.pending_absence
        if absence is None or absence.older_page != snapshot.page:
            return None
        if not (
            contains_post_id(self.coverage, absence.newer_boundary_post_id)
            and contains_post_id(self.coverage, absence.older_boundary_post_id)
        ):
            # Both sides must already be authoritative finalized coverage.
            self.pending_absence = None
            return None

        if any(
            item.post_id == absence.post_id for item in self.coverage_absences
        ):
            self.pending_absence = None
            return None
        if self.absence_repository:
            absence = self.absence_repository.record(absence)
        self.coverage_absences.append(absence)
        self.pending_absence = None
        return absence

    def _update_summary(self, summary: PhaseSummary, snapshot: PageSnapshot) -> None:
        summary.scanned_pages += 1
        summary.scanned_posts += len(snapshot.posts)
        summary.pages.append(snapshot.page)
        oldest = snapshot.oldest_created_at.astimezone(timezone.utc).isoformat()
        if not summary.oldest_seen_at or oldest < summary.oldest_seen_at:
            summary.oldest_seen_at = oldest

    def _record_summary(self, summary: PhaseSummary) -> None:
        if not self.client:
            return
        record_run(
            self.client,
            target=self.target,
            status=summary.status,
            scanned_pages=summary.scanned_pages,
            scanned_posts=summary.scanned_posts,
            matched_posts=summary.matched_posts,
            run_started_at=self.run_started_at,
            error_message=summary.metadata(),
            ensure_source=False,
            run_type=summary.run_type,
        )

    def _recent_persistence_fits(
        self,
        eligible: Sequence[DcinsidePost],
    ) -> bool:
        """Keep the configured historical window available in the worst case.

        D1 writes are intentionally performed before coverage. When the
        remaining wall-clock budget cannot cover their configured HTTP
        timeouts, recent finalization is deferred instead of borrowing the
        historical lane's reserved time.
        """
        # Even dry runs keep the same lane boundary so timing behavior remains
        # representative. With D1 enabled, include the write upper bound too.
        if self.runtime.remaining_seconds() <= self.config.deep_reserved_seconds:
            return False
        return self._persistence_fits(
            query_count=self._finalization_query_count(eligible),
            reserve_seconds=self.config.deep_reserved_seconds,
        )

    def _finalization_persistence_fits(
        self,
        eligible: Sequence[DcinsidePost],
        *,
        extra_queries: int = 0,
    ) -> bool:
        return self._persistence_fits(
            query_count=(
                self._finalization_query_count(eligible)
                + max(0, extra_queries)
            ),
        )

    def _finalization_query_count(
        self,
        eligible: Sequence[DcinsidePost],
    ) -> int:
        if not eligible:
            return 0

        preview = interval_from_posts(
            source_key=self.target.key,
            posts=eligible,
            checked_at=self.run_started_at,
        )
        if interval_is_covered(preview, self.coverage):
            # _commit_finalized_page returns before issuing any D1 query.
            return 0

        merge_result = merge_scanned_interval(self.coverage, preview)
        coverage_query_count = 1 + sum(
            interval.key != merge_result.merged.key
            for interval in merge_result.superseded
        )

        # Existing-ID lookup + worst-case bounded post upserts + the exact
        # coverage INSERT/DELETE plan. record_scanned receives self.coverage,
        # so it does not issue a separate coverage SELECT.
        return (
            existing_post_lookup_query_count(len(eligible))
            + post_upsert_query_count(len(eligible))
            + coverage_query_count
        )

    def _persistence_fits(
        self,
        *,
        query_count: int,
        reserve_seconds: float = 0.0,
    ) -> bool:
        if not self.client:
            return True
        d1_timeout = max(1.0, float(getattr(self.client, "timeout_seconds", 1.0)))
        persistence_budget = d1_timeout * max(0, query_count)
        return self.runtime.remaining_seconds() > (
            max(0.0, reserve_seconds) + persistence_budget
        )

    def _mark_history_frontier_exhausted(
        self,
        snapshot: PageSnapshot,
        covered_interval: CoverageInterval,
    ) -> bool:
        """Persist only proof that combines fresh head and final-page evidence."""
        if (
            self.observed_head_last_page is None
            or self.observed_head_last_page < snapshot.page
            or not snapshot.navigation_valid
            or not snapshot.can_prove_last_page
            or snapshot.current_page != snapshot.page
            or snapshot.last_page != snapshot.page
            or not interval_is_covered(covered_interval, self.coverage)
        ):
            return False

        self.source_state.state_metadata["history_frontier_exhausted"] = True
        self.source_state.state_metadata["history_last_page"] = snapshot.page
        return True

    def _restore_block_cooldown_from_runs(self) -> None:
        """Recover fail-closed cooldown state from a successfully logged block."""
        if not self.client:
            return

        saved_until = parse_optional_datetime(self.source_state.blocked_until)
        if saved_until and saved_until > self.cycle_started_at:
            return

        rows = self.client.query(
            """
            SELECT started_at, finished_at
            FROM crawl_runs
            WHERE source_key = ?
              AND status = 'blocked'
            ORDER BY id DESC
            LIMIT 1
            """,
            [self.target.key],
        )
        if not rows:
            return

        blocked_at = parse_optional_datetime(
            str(rows[0].get("finished_at") or rows[0].get("started_at") or "")
        )
        if not blocked_at:
            return

        recovered_until = blocked_at + timedelta(
            hours=self.config.block_cooldown_hours
        )
        if recovered_until > self.cycle_started_at:
            self.source_state.blocked_until = recovered_until.astimezone(
                timezone.utc
            ).isoformat()

    def _mark_active_phase(self, status: str, reason: str) -> None:
        if not self.summaries:
            return
        active = self.summaries[-1]
        active.status = status
        active.stop_reason = reason[:500]

    def _timeout_streak(self) -> int:
        return positive_int(
            self.source_state.state_metadata.get(TIMEOUT_STREAK_METADATA_KEY),
            default=0,
        )

    def _persist_timeout_streak(self, streak: int) -> None:
        """Persist only the shared counter after an aborted source action.

        A timed-out Backfill may have changed page-hint metadata in memory before
        the failing request. Saving the complete SourceState here would make
        those unfinished hints authoritative, so the failure path updates only
        the counter inside the existing JSON metadata column.
        """

        self.source_state.state_metadata[TIMEOUT_STREAK_METADATA_KEY] = streak
        if not self.client:
            return
        self.client.query(
            """
            UPDATE source_state
            SET state_metadata = json_set(
                  CASE
                    WHEN json_valid(state_metadata) THEN state_metadata
                    ELSE '{}'
                  END,
                  '$.consecutive_timeout_cycles',
                  ?
                ),
                updated_at = ?
            WHERE source_key = ?
            """,
            [streak, utc_now(), self.target.key],
        )

    def _handle_exhausted_timeout(
        self,
        exc: CrawlTimeoutError,
    ) -> Dict[str, object]:
        streak = self._timeout_streak() + 1
        timeout_reason = f"Consecutive timeout cycle {streak}: {exc}"
        try:
            self._persist_timeout_streak(streak)
        except Exception as save_exc:
            reason = f"Could not persist consecutive timeout state: {save_exc}"
            self._mark_active_phase("failed", reason)
            self._record_failure(reason, self.summaries)
            return self._result("failed", self.summaries, reason)

        if streak >= TIMEOUT_FAILURE_THRESHOLD:
            self._mark_active_phase("failed", timeout_reason)
            self._record_failure(timeout_reason, self.summaries)
            return self._result("failed", self.summaries, timeout_reason)

        self._mark_active_phase("partial", timeout_reason)
        self._record_timeout_warning(timeout_reason, self.summaries)
        return self._result("partial", self.summaries, timeout_reason)

    def _record_timeout_warning(
        self,
        reason: str,
        summaries: Sequence[PhaseSummary],
    ) -> None:
        if self.client:
            try:
                record_run(
                    self.client,
                    target=self.target,
                    status="partial",
                    scanned_pages=sum(item.scanned_pages for item in summaries),
                    scanned_posts=sum(item.scanned_posts for item in summaries),
                    matched_posts=sum(item.matched_posts for item in summaries),
                    run_started_at=self.run_started_at,
                    error_message=reason,
                    ensure_source=False,
                    run_type="crawl_cycle",
                )
            except Exception as record_exc:
                self.persistence_warnings.append(
                    f"Could not record timeout warning: {record_exc}"
                )

    def _record_block(self, reason: str, summaries: Sequence[PhaseSummary]) -> None:
        timestamp = utc_now()
        blocked_at = parse_optional_datetime(timestamp) or datetime.now(timezone.utc)
        self.source_state.last_blocked_at = timestamp
        self.source_state.last_block_reason = reason[:500]
        self.source_state.blocked_until = (
            blocked_at
            + timedelta(hours=self.config.block_cooldown_hours)
        ).astimezone(timezone.utc).isoformat()
        if self.client:
            try:
                save_source_state(self.client, self.source_state)
            except Exception as exc:
                self.persistence_warnings.append(f"Could not save block cooldown: {exc}")
            try:
                record_run(
                    self.client,
                    target=self.target,
                    status="blocked",
                    scanned_pages=sum(item.scanned_pages for item in summaries),
                    scanned_posts=sum(item.scanned_posts for item in summaries),
                    matched_posts=sum(item.matched_posts for item in summaries),
                    run_started_at=self.run_started_at,
                    error_message=reason,
                    ensure_source=False,
                    run_type="crawl_cycle",
                )
            except Exception as exc:
                self.persistence_warnings.append(f"Could not record blocked run: {exc}")

    def _record_failure(self, reason: str, summaries: Sequence[PhaseSummary]) -> None:
        if self.client:
            try:
                record_run(
                    self.client,
                    target=self.target,
                    status="failed",
                    scanned_pages=sum(item.scanned_pages for item in summaries),
                    scanned_posts=sum(item.scanned_posts for item in summaries),
                    matched_posts=sum(item.matched_posts for item in summaries),
                    run_started_at=self.run_started_at,
                    error_message=reason,
                    ensure_source=False,
                    run_type="crawl_cycle",
                )
            except Exception as exc:
                self.persistence_warnings.append(f"Could not record failed run: {exc}")

    def _result(
        self,
        status: str,
        summaries: Sequence[PhaseSummary],
        error: str = "",
    ) -> Dict[str, object]:
        return {
            "target": self.target.key,
            "mode": self.mode,
            "status": status,
            "persisted": bool(self.client),
            "cycle_started_at": self.run_started_at,
            "hot_cutoff": self.hot_cutoff.astimezone(timezone.utc).isoformat(),
            "finalization_cutoff": self.finalization_cutoff.astimezone(timezone.utc).isoformat(),
            "source_requests": self.runtime.request_count,
            "blocked_reason": self.runtime.blocked_reason or "",
            "persistence_warnings": self.persistence_warnings,
            "error": error,
            "phases": [asdict(item) for item in summaries],
        }


def interval_from_posts(
    *,
    source_key: str,
    posts: Sequence[DcinsidePost],
    checked_at: str,
) -> CoverageInterval:
    if not posts:
        raise ValueError("Cannot create coverage from an empty post sequence")
    ordered = sorted(posts, key=lambda post: int(post.external_post_id))
    return CoverageInterval(
        source_key=source_key,
        oldest_post_id=int(ordered[0].external_post_id),
        newest_post_id=int(ordered[-1].external_post_id),
        oldest_created_at=ordered[0].created_at,
        newest_created_at=ordered[-1].created_at,
        checked_at=checked_at,
        created_at=checked_at,
        updated_at=checked_at,
    )


def interval_is_covered(
    target: CoverageInterval,
    intervals: Sequence[CoverageInterval],
) -> bool:
    return any(
        interval.source_key == target.source_key
        and interval.oldest_post_id <= target.oldest_post_id
        and interval.newest_post_id >= target.newest_post_id
        for interval in intervals
    )


def select_history_target(
    intervals: Sequence[CoverageInterval],
    *,
    prefer_gap: bool,
    after_gap_id: Optional[int] = None,
) -> tuple[Optional[int], str]:
    if not intervals:
        return None, "bootstrap"

    ordered = sorted(intervals, key=lambda item: item.newest_post_id, reverse=True)
    gaps: List[int] = []
    for newer, older in zip(ordered, ordered[1:]):
        if newer.oldest_post_id > older.newest_post_id + 1:
            gaps.append(newer.oldest_post_id - 1)

    if prefer_gap and gaps:
        if after_gap_id is not None:
            for gap_target in gaps:
                if gap_target < after_gap_id:
                    return gap_target, "gap"
        return gaps[0], "gap"
    frontier = min(interval.oldest_post_id for interval in ordered) - 1
    if frontier <= 0:
        return None, "complete"
    return frontier, "frontier"


def count_qualifying(posts: Sequence[DcinsidePost], target: TargetBoard) -> int:
    return sum(
        1
        for post in posts
        if is_qualifying_post(post, target.min_upvotes, target.min_comments)
    )


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return ensure_aware(parsed)


def parse_optional_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def finalization_eligibility_is_id_suffix(
    posts: Sequence[DcinsidePost], cutoff: datetime
) -> bool:
    """Require finalized rows to be a suffix in canonical descending-ID order."""

    eligible_seen = False
    for post in sorted(
        posts, key=lambda item: int(item.external_post_id), reverse=True
    ):
        eligible = parse_datetime(post.created_at) <= cutoff
        if eligible:
            eligible_seen = True
        elif eligible_seen:
            return False
    return True


def navigation_is_clamped_to_earlier_page(
    navigation: DcinsideNavigationDiagnostics,
    *,
    requested_page: int,
) -> bool:
    """Recognize DCInside's exact beyond-the-end page clamp."""

    current_page = navigation.current_page
    return (
        requested_page > 1
        and navigation.requested_page == requested_page
        and current_page is not None
        and 0 < current_page < requested_page
        and navigation.paging_container_seen
        and navigation.paging_container_count >= 1
        and (
            navigation.paging_container_closed_count
            == navigation.paging_container_count
        )
        and [error.code for error in navigation.errors]
        == ["requested_page_mismatch"]
        and navigation_page_relationships_are_valid(navigation)
    )


def board_id_from_url(board_url: str) -> str:
    values = parse_qs(
        urlparse(board_url).query,
        keep_blank_values=True,
    ).get("id", [])
    if len(values) != 1 or not values[0].strip():
        raise ValueError("Target board URL must contain exactly one non-empty id")
    return values[0].strip()


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def positive_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def env_float(name: str, default: float) -> float:
    raw = get_env(name, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    raw = get_env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def config_from_env() -> CycleConfig:
    return CycleConfig(
        finalization_age_hours=env_float(
            "TC_FINALIZATION_AGE_HOURS", DEFAULT_FINALIZATION_AGE_HOURS
        ),
        hot_lookback_minutes=env_float(
            "TC_HOT_LOOKBACK_MINUTES", DEFAULT_HOT_LOOKBACK_MINUTES
        ),
        hot_max_seconds=env_float("TC_HOT_MAX_SECONDS", DEFAULT_HOT_MAX_SECONDS),
        cycle_max_seconds=env_float("TC_CYCLE_MAX_SECONDS", DEFAULT_CYCLE_MAX_SECONDS),
        min_request_interval_seconds=env_float(
            "TC_REQUEST_INTERVAL_SECONDS", DEFAULT_MIN_REQUEST_INTERVAL_SECONDS
        ),
        deep_reserved_seconds=env_float(
            "TC_DEEP_RESERVED_SECONDS", DEFAULT_DEEP_RESERVED_SECONDS
        ),
        block_cooldown_hours=env_float(
            "TC_BLOCK_COOLDOWN_HOURS", DEFAULT_BLOCK_COOLDOWN_HOURS
        ),
        transient_fetch_attempts=env_int(
            "TC_TRANSIENT_FETCH_ATTEMPTS", DEFAULT_TRANSIENT_FETCH_ATTEMPTS
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one safe TodayCommunity hot-scan/finalization cycle."
    )
    parser.add_argument("--target", default="dcinside-singularity")
    parser.add_argument(
        "--mode",
        choices=VALID_CYCLE_MODES,
        default=CYCLE_MODE_FULL,
        help="Run the full cycle, only the hot scan, or only finalization/backfill.",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        default=is_truthy(get_env("TC_PERSIST", "0")),
    )
    return parser.parse_args()


def status_requires_failure_exit(status: str) -> bool:
    return status in {"blocked", "failed"}


def main() -> None:
    args = parse_args()
    config = config_from_env()
    target = get_target(args.target)
    client = None
    if args.persist:
        client = D1Client(
            account_id=get_required_env("TC_CF_ACCOUNT_ID"),
            database_id=get_required_env("TC_CF_DATABASE_ID"),
            api_token=get_required_env("TC_CF_API_TOKEN"),
        )

    runtime = CycleRuntime(
        min_request_interval_seconds=config.min_request_interval_seconds,
        total_seconds=config.cycle_max_seconds,
        hot_seconds=config.hot_max_seconds,
    )
    result = CrawlCycle(
        target=target,
        config=config,
        runtime=runtime,
        client=client,
        mode=args.mode,
    ).run()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if status_requires_failure_exit(str(result["status"])):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
