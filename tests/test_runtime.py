from __future__ import annotations

import unittest

from crawler.runtime import (
    BACKFILL_PHASE,
    HOT_PHASE,
    CycleBlocked,
    CycleRuntime,
    RuntimeLimitReached,
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = float(start)
        self.sleep_calls = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise AssertionError("sleep duration cannot be negative")
        self.sleep_calls.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_runtime(clock: FakeClock, **overrides) -> CycleRuntime:
    options = {
        "min_request_interval_seconds": 2.0,
        "total_seconds": 25.0,
        "hot_seconds": 5.0,
        "monotonic": clock.monotonic,
        "sleep": clock.sleep,
    }
    options.update(overrides)
    return CycleRuntime(**options)


class CycleRuntimeTests(unittest.TestCase):
    def test_first_request_is_immediate_and_followups_share_global_spacing(self) -> None:
        clock = FakeClock(start=100.0)
        runtime = make_runtime(
            clock,
            min_request_interval_seconds=3.0,
            total_seconds=100.0,
            hot_seconds=20.0,
        )

        first = runtime.acquire_request(HOT_PHASE)
        self.assertEqual([], clock.sleep_calls)
        second = runtime.acquire_request(HOT_PHASE)
        clock.advance(1.0)
        self.assertEqual(2.0, runtime.next_request_delay_seconds())
        third = runtime.acquire_request(BACKFILL_PHASE)

        self.assertEqual([3.0, 2.0], clock.sleep_calls)
        self.assertEqual(100.0, first.started_at)
        self.assertEqual(103.0, second.started_at)
        self.assertEqual(106.0, third.started_at)
        self.assertEqual(3, runtime.request_count)
        self.assertEqual(2, runtime.phase_request_count(HOT_PHASE))
        self.assertEqual(1, runtime.phase_request_count(BACKFILL_PHASE))

    def test_polite_interval_can_start_after_response_completion(self) -> None:
        clock = FakeClock(start=10.0)
        runtime = make_runtime(
            clock,
            min_request_interval_seconds=5.0,
            total_seconds=100.0,
            hot_seconds=50.0,
        )
        runtime.acquire_request(HOT_PHASE)
        clock.advance(4.0)
        runtime.complete_request()
        runtime.acquire_request(HOT_PHASE)

        self.assertEqual([5.0], clock.sleep_calls)
        self.assertEqual(19.0, clock.now)

    def test_hot_deadline_stops_phase_without_sleeping_past_it(self) -> None:
        clock = FakeClock()
        runtime = make_runtime(
            clock,
            min_request_interval_seconds=5.0,
            total_seconds=25.0,
            hot_seconds=5.0,
        )
        runtime.acquire_request(HOT_PHASE)
        clock.advance(1.0)

        with self.assertRaises(RuntimeLimitReached) as caught:
            runtime.acquire_request(HOT_PHASE)

        self.assertEqual("phase", caught.exception.scope)
        self.assertEqual("hot_deadline", caught.exception.reason)
        self.assertEqual([], clock.sleep_calls)
        self.assertEqual(1, runtime.request_count)

    def test_equal_hot_and_cycle_deadline_is_a_normal_hot_phase_stop(self) -> None:
        clock = FakeClock()
        runtime = make_runtime(
            clock,
            min_request_interval_seconds=1.0,
            total_seconds=5.0,
            hot_seconds=5.0,
        )
        clock.advance(5.0)

        with self.assertRaises(RuntimeLimitReached) as caught:
            runtime.acquire_request(HOT_PHASE)

        self.assertEqual("phase", caught.exception.scope)
        self.assertEqual("hot_deadline", caught.exception.reason)

    def test_equal_deadline_spacing_guard_is_a_normal_hot_phase_stop(self) -> None:
        clock = FakeClock()
        runtime = make_runtime(
            clock,
            min_request_interval_seconds=5.0,
            total_seconds=5.0,
            hot_seconds=5.0,
        )
        runtime.acquire_request(HOT_PHASE)

        with self.assertRaises(RuntimeLimitReached) as caught:
            runtime.acquire_request(HOT_PHASE)

        self.assertEqual("phase", caught.exception.scope)
        self.assertEqual("hot_deadline", caught.exception.reason)
        self.assertEqual([], clock.sleep_calls)

    def test_cycle_deadline_stops_backfill(self) -> None:
        clock = FakeClock(start=12.0)
        runtime = make_runtime(
            clock,
            min_request_interval_seconds=0.1,
            total_seconds=10.0,
            hot_seconds=2.0,
        )
        clock.advance(10.0)

        with self.assertRaises(RuntimeLimitReached) as caught:
            runtime.acquire_request(BACKFILL_PHASE)

        self.assertEqual("cycle", caught.exception.scope)
        self.assertEqual("cycle_deadline", caught.exception.reason)
        self.assertEqual(0, runtime.request_count)

    def test_request_count_is_telemetry_not_a_cycle_cap(self) -> None:
        clock = FakeClock()
        runtime = make_runtime(
            clock,
            min_request_interval_seconds=0.1,
            total_seconds=100.0,
            hot_seconds=50.0,
        )
        for _ in range(12):
            runtime.acquire_request(BACKFILL_PHASE)

        self.assertEqual(12, runtime.request_count)
        self.assertEqual(12, runtime.phase_request_count(BACKFILL_PHASE))
        self.assertLess(clock.now, runtime.cycle_deadline)

    def test_hot_deadline_not_request_count_reserves_backfill_time(self) -> None:
        clock = FakeClock()
        runtime = make_runtime(
            clock,
            min_request_interval_seconds=0.1,
            total_seconds=10.0,
            hot_seconds=3.0,
        )
        for _ in range(12):
            runtime.acquire_request(HOT_PHASE)
        clock.advance(2.0)

        with self.assertRaises(RuntimeLimitReached) as caught:
            runtime.acquire_request(HOT_PHASE)

        self.assertEqual("phase", caught.exception.scope)
        self.assertEqual("hot_deadline", caught.exception.reason)

        runtime.acquire_request(BACKFILL_PHASE)
        self.assertEqual(13, runtime.request_count)
        self.assertEqual(1, runtime.phase_request_count(BACKFILL_PHASE))

    def test_fake_clock_reports_phase_and_cycle_time_remaining(self) -> None:
        clock = FakeClock(start=50.0)
        runtime = make_runtime(clock, total_seconds=25.0, hot_seconds=5.0)

        clock.advance(3.5)

        self.assertEqual(1.5, runtime.remaining_seconds(HOT_PHASE))
        self.assertEqual(21.5, runtime.remaining_seconds(BACKFILL_PHASE))
        self.assertEqual(21.5, runtime.remaining_seconds())

    def test_block_is_terminal_and_propagates_to_every_phase(self) -> None:
        clock = FakeClock()
        runtime = make_runtime(clock)
        runtime.acquire_request(HOT_PHASE)

        with self.assertRaises(CycleBlocked) as initial:
            runtime.block("HTTP 429")

        self.assertEqual("HTTP 429", initial.exception.reason)
        self.assertTrue(runtime.is_blocked)
        self.assertEqual("HTTP 429", runtime.blocked_reason)

        for phase in (HOT_PHASE, BACKFILL_PHASE):
            with self.assertRaises(CycleBlocked) as propagated:
                runtime.acquire_request(phase)
            self.assertEqual("HTTP 429", propagated.exception.reason)

        self.assertEqual(1, runtime.request_count)
        self.assertEqual([], clock.sleep_calls)

    def test_invalid_timing_configuration_is_rejected(self) -> None:
        clock = FakeClock()

        with self.assertRaises(ValueError):
            make_runtime(clock, min_request_interval_seconds=-1.0)
        with self.assertRaises(ValueError):
            make_runtime(clock, min_request_interval_seconds=0.0)
        with self.assertRaises(ValueError):
            make_runtime(clock, total_seconds=5.0, hot_seconds=6.0)


if __name__ == "__main__":
    unittest.main()
