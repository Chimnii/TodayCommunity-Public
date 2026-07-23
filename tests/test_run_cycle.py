from __future__ import annotations

import json
import re
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional
from unittest.mock import patch

from crawler.coverage import (
    CoverageAbsence,
    CoverageInterval,
    normalize_effective_coverage,
)
from crawler.jobs.run_cycle import (
    CYCLE_MODE_BACKFILL,
    CYCLE_MODE_HOT,
    CrawlCycle,
    CycleConfig,
    TIMEOUT_STREAK_METADATA_KEY,
    finalization_eligibility_is_id_suffix,
    select_history_target,
    status_requires_failure_exit,
)
from crawler.jobs.scan_new_posts import (
    CrawlBlockedError,
    CrawlSourceError,
    CrawlTimeoutError,
    CrawlTransientError,
    update_finalized_posts,
    upsert_posts,
)
from crawler.parsers.dcinside import DcinsidePost
from crawler.runtime import (
    BACKFILL_PHASE,
    HOT_PHASE,
    CycleRuntime,
    RuntimeLimitReached,
)
from crawler.targets import get_target


FIXED_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


def row(
    post_id: int,
    created_at_kst: str,
    upvotes: int = 0,
    comments: int = 0,
    title_markup: Optional[str] = None,
) -> str:
    title = title_markup if title_markup is not None else f"post {post_id}"
    return f"""
    <tr class="ub-content" data-no="{post_id}" data-type="icon_txt">
      <td class="gall_subject">일반</td>
      <td class="gall_tit"><a href="/mgallery/board/view/?id=thesingularity&amp;no={post_id}">{title}</a><span class="reply_num">[{comments}]</span></td>
      <td class="gall_date" title="{created_at_kst}">07.16</td>
      <td class="gall_recommend">{upvotes}</td>
    </tr>
    """


def page_html(*rows: str) -> str:
    return "<table>" + "".join(rows) + "</table>"


def pagination_html(current_page: int, last_page: int) -> str:
    links = [f'<em>{current_page}</em>']
    if current_page < last_page:
        links.append(
            '<a class="page_next" '
            f'href="/mgallery/board/lists/?id=thesingularity&amp;page={current_page + 1}">'
            '다음</a>'
        )
    links.append(
        '<a class="page_end" '
        f'href="/mgallery/board/lists/?id=thesingularity&amp;page={last_page}">'
        '끝</a>'
    )
    return '<div class="bottom_paging_box">' + "".join(links) + "</div>"


class MappingFetcher:
    def __init__(
        self,
        pages,
        blocked_page: Optional[int] = None,
        *,
        last_page: int = 100000,
        last_pages=None,
        rendered_pages=None,
        include_navigation: bool = True,
    ) -> None:
        self.pages = pages
        self.blocked_page = blocked_page
        self.last_page = last_page
        self.last_pages = last_pages or {}
        self.rendered_pages = rendered_pages or {}
        self.include_navigation = include_navigation
        self.requested_pages = []

    def __call__(self, url: str, timeout_seconds: float) -> str:
        match = re.search(r"[?&]page=(\d+)", url)
        page = int(match.group(1)) if match else 1
        self.requested_pages.append(page)
        if page == self.blocked_page:
            raise CrawlBlockedError("test block")
        html = self.pages.get(page, self.pages[max(self.pages)])
        if not self.include_navigation:
            return html
        rendered_page = self.rendered_pages.get(page, page)
        last_page = self.last_pages.get(page, self.last_page)
        return html + pagination_html(rendered_page, last_page)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TimedFetcher(MappingFetcher):
    def __init__(self, pages, clock: FakeClock) -> None:
        super().__init__(pages)
        self.clock = clock
        self.timeouts = []

    def __call__(self, url: str, timeout_seconds: float) -> str:
        self.timeouts.append(timeout_seconds)
        self.clock.advance(timeout_seconds)
        return super().__call__(url, timeout_seconds)


class SqliteClient:
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        schema = (
            Path(__file__).resolve().parents[1] / "cloudflare" / "schema.sql"
        ).read_text(encoding="utf-8")
        self.connection.executescript(schema)

    def query(self, sql: str, params: Optional[Iterable[object]] = None):
        cursor = self.connection.execute(sql, list(params or []))
        if cursor.description:
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        self.connection.commit()
        return []


class FailingPostClient(SqliteClient):
    def query(self, sql: str, params: Optional[Iterable[object]] = None):
        if "INSERT INTO posts" in sql:
            raise RuntimeError("injected post write failure")
        return super().query(sql, params)


class FailingSecondPostBatchClient(SqliteClient):
    def __init__(self) -> None:
        super().__init__()
        self.post_write_calls = 0

    def query(self, sql: str, params: Optional[Iterable[object]] = None):
        if "INSERT INTO posts" in sql:
            self.post_write_calls += 1
            if self.post_write_calls == 2:
                raise RuntimeError("injected second post batch failure")
        return super().query(sql, params)


class FailingAbsenceClient(SqliteClient):
    def query(self, sql: str, params: Optional[Iterable[object]] = None):
        if "INSERT INTO coverage_absences" in sql:
            raise RuntimeError("injected absence write failure")
        return super().query(sql, params)


class FailingAbsenceDeleteClient(SqliteClient):
    def query(self, sql: str, params: Optional[Iterable[object]] = None):
        if "DELETE FROM coverage_absences" in sql:
            raise RuntimeError("injected absence delete failure")
        return super().query(sql, params)


class FailingSourceStateWriteClient(SqliteClient):
    def __init__(self) -> None:
        super().__init__()
        self.fail_source_state_writes = False

    def query(self, sql: str, params: Optional[Iterable[object]] = None):
        if self.fail_source_state_writes and "source_state" in sql and (
            "UPDATE source_state" in sql or "INSERT INTO source_state" in sql
        ):
            raise RuntimeError("injected source state write failure")
        return super().query(sql, params)


def config() -> CycleConfig:
    return CycleConfig(
        finalization_age_hours=24,
        hot_lookback_minutes=60,
        hot_max_seconds=300,
        cycle_max_seconds=1500,
        min_request_interval_seconds=0.001,
    )


def runtime(settings: CycleConfig) -> CycleRuntime:
    return CycleRuntime(
        min_request_interval_seconds=settings.min_request_interval_seconds,
        total_seconds=settings.cycle_max_seconds,
        hot_seconds=settings.hot_max_seconds,
    )


def source_state_metadata(client: SqliteClient) -> dict:
    rows = client.query(
        "SELECT state_metadata FROM source_state WHERE source_key = ?",
        ["dcinside-singularity"],
    )
    return json.loads(rows[0]["state_metadata"]) if rows else {}


class ExitStatusTests(unittest.TestCase):
    def test_only_blocked_and_failed_results_fail_the_action(self) -> None:
        for status in ("completed", "partial", "cooldown"):
            with self.subTest(status=status):
                self.assertFalse(status_requires_failure_exit(status))

        for status in ("blocked", "failed"):
            with self.subTest(status=status):
                self.assertTrue(status_requires_failure_exit(status))


def post(post_id: int, created_at: str, upvotes: int = 4) -> DcinsidePost:
    return DcinsidePost(
        external_post_id=str(post_id),
        subject="일반",
        title=f"post {post_id}",
        post_url=f"https://example.com/{post_id}",
        created_at=created_at,
        created_at_raw=created_at,
        upvotes=upvotes,
        comments=0,
        qualifies_by="upvotes" if upvotes >= 4 else "none",
    )


class CrawlCycleTests(unittest.TestCase):
    def test_operational_defaults_match_the_allocated_cycle_windows(self) -> None:
        settings = CycleConfig()

        self.assertEqual(settings.finalization_age_hours, 12)
        self.assertEqual(settings.block_cooldown_hours, 6)
        self.assertEqual(settings.hot_max_seconds, 7 * 60)
        self.assertEqual(settings.cycle_max_seconds - settings.hot_max_seconds, 13 * 60)
        self.assertEqual(settings.min_request_interval_seconds, 10)

    def test_cycle_config_rejects_nonpositive_request_interval(self) -> None:
        with self.assertRaises(ValueError):
            CycleConfig(min_request_interval_seconds=0)
        with self.assertRaises(ValueError):
            CycleConfig(
                hot_max_seconds=600,
                cycle_max_seconds=900,
                deep_reserved_seconds=300,
            )

    def test_dedicated_hot_accepts_one_shared_180_second_window(self) -> None:
        settings = CycleConfig(
            hot_lookback_minutes=180,
            hot_max_seconds=180,
            cycle_max_seconds=180,
            deep_reserved_seconds=0,
        )

        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=MappingFetcher(
                {1: page_html(row(1000, "2026-07-16 17:00:00"))}
            ),
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        )

        result = cycle.run()

        self.assertEqual(result["mode"], CYCLE_MODE_HOT)
        self.assertEqual(
            [phase["run_type"] for phase in result["phases"]],
            ["hot_scan"],
        )
        self.assertEqual(cycle.runtime.phase_request_count(BACKFILL_PHASE), 0)

    def test_full_and_backfill_modes_require_a_history_reservation(self) -> None:
        settings = CycleConfig(
            hot_max_seconds=180,
            cycle_max_seconds=180,
            deep_reserved_seconds=0,
        )

        with self.assertRaisesRegex(ValueError, "history reservation"):
            CrawlCycle(
                target=get_target("dcinside-singularity"),
                config=settings,
                runtime=runtime(settings),
                fetcher=MappingFetcher({}),
                cycle_started_at=FIXED_NOW,
                mode=CYCLE_MODE_BACKFILL,
            )

    def test_unknown_crawl_mode_is_rejected(self) -> None:
        settings = config()

        with self.assertRaisesRegex(ValueError, "unknown crawl mode"):
            CrawlCycle(
                target=get_target("dcinside-singularity"),
                config=settings,
                runtime=runtime(settings),
                fetcher=MappingFetcher({}),
                cycle_started_at=FIXED_NOW,
                mode="unknown",
            )

    def test_transient_fetch_is_retried_once_with_shared_request_spacing(self) -> None:
        clock = FakeClock()
        attempts = []

        def transient_fetcher(url: str, timeout_seconds: float) -> str:
            attempts.append((url, timeout_seconds, clock.monotonic()))
            if len(attempts) == 1:
                raise CrawlTransientError("temporary timeout")
            return page_html(row(100, "2026-07-16 20:00:00"))

        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=300,
            cycle_max_seconds=1500,
            min_request_interval_seconds=20,
            transient_fetch_attempts=2,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=20,
            total_seconds=1500,
            hot_seconds=300,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=transient_fetcher,
            cycle_started_at=FIXED_NOW,
        )

        snapshot = cycle._fetch_page(1, HOT_PHASE)

        self.assertEqual([post.external_post_id for post in snapshot.posts], ["100"])
        self.assertEqual(len(attempts), 2)
        self.assertEqual([attempt[2] for attempt in attempts], [0.0, 20.0])
        self.assertEqual(cycle_runtime.request_count, 2)

    def test_transient_retry_does_not_start_inside_deadline_guard(self) -> None:
        clock = FakeClock()
        attempts = []

        def transient_fetcher(url: str, timeout_seconds: float) -> str:
            attempts.append(clock.monotonic())
            raise CrawlTransientError("temporary timeout")

        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=21,
            cycle_max_seconds=60,
            min_request_interval_seconds=20,
            deep_reserved_seconds=10,
            transient_fetch_attempts=2,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=20,
            total_seconds=60,
            hot_seconds=21,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=transient_fetcher,
            cycle_started_at=FIXED_NOW,
        )

        with self.assertRaises(RuntimeLimitReached):
            cycle._fetch_page(1, HOT_PHASE)

        self.assertEqual(attempts, [0.0])
        self.assertEqual(cycle_runtime.request_count, 1)

    def test_exhausted_timeout_is_not_reclassified_as_runtime_limit(self) -> None:
        clock = FakeClock()
        attempts = []

        def timeout_fetcher(url: str, timeout_seconds: float) -> str:
            attempts.append(clock.monotonic())
            raise CrawlTimeoutError("temporary timeout")

        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=21,
            cycle_max_seconds=60,
            min_request_interval_seconds=20,
            deep_reserved_seconds=10,
            transient_fetch_attempts=2,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=20,
            total_seconds=60,
            hot_seconds=21,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=timeout_fetcher,
            cycle_started_at=FIXED_NOW,
        )

        with self.assertRaises(CrawlTimeoutError):
            cycle._fetch_page(1, HOT_PHASE)

        self.assertEqual(attempts, [0.0])
        self.assertEqual(cycle_runtime.request_count, 1)

    def test_transient_fetch_retry_limit_is_bounded(self) -> None:
        attempts = []

        def failing_fetcher(url: str, timeout_seconds: float) -> str:
            attempts.append((url, timeout_seconds))
            raise CrawlTransientError("temporary timeout")

        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=300,
            cycle_max_seconds=1500,
            min_request_interval_seconds=0.001,
            transient_fetch_attempts=2,
        )
        cycle_runtime = runtime(settings)
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=failing_fetcher,
            cycle_started_at=FIXED_NOW,
        )

        with self.assertRaises(CrawlTransientError):
            cycle._fetch_page(1, HOT_PHASE)

        self.assertEqual(len(attempts), 2)
        self.assertEqual(cycle_runtime.request_count, 2)

    def test_timeout_streak_is_shared_across_modes_and_fails_from_third(self) -> None:
        client = SqliteClient()
        settings = config()
        attempts = []

        def timeout_fetcher(url: str, timeout_seconds: float) -> str:
            attempts.append(url)
            raise CrawlTimeoutError("temporary timeout")

        statuses = []
        for mode in (
            CYCLE_MODE_HOT,
            CYCLE_MODE_BACKFILL,
            CYCLE_MODE_HOT,
            CYCLE_MODE_BACKFILL,
        ):
            result = CrawlCycle(
                target=get_target("dcinside-singularity"),
                config=settings,
                runtime=runtime(settings),
                client=client,
                fetcher=timeout_fetcher,
                cycle_started_at=FIXED_NOW,
                mode=mode,
            ).run()
            statuses.append(result["status"])

        self.assertEqual(statuses, ["partial", "partial", "failed", "failed"])
        self.assertEqual(len(attempts), 8)
        self.assertEqual(
            source_state_metadata(client)[TIMEOUT_STREAK_METADATA_KEY],
            4,
        )
        self.assertEqual(
            client.query(
                "SELECT status FROM crawl_runs ORDER BY id"
            ),
            [
                {"status": "partial"},
                {"status": "partial"},
                {"status": "failed"},
                {"status": "failed"},
            ],
        )

    def test_timeout_counter_write_does_not_commit_unfinished_state_hints(self) -> None:
        client = SqliteClient()
        settings = config()

        def timeout_fetcher(url: str, timeout_seconds: float) -> str:
            raise CrawlTimeoutError("temporary timeout")

        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=timeout_fetcher,
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_BACKFILL,
        )
        cycle.source_state.state_metadata["unfinished_backfill_hint"] = 99

        result = cycle.run()

        persisted_metadata = source_state_metadata(client)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(persisted_metadata[TIMEOUT_STREAK_METADATA_KEY], 1)
        self.assertNotIn("unfinished_backfill_hint", persisted_metadata)

    def test_normal_partial_and_completed_cycles_reset_timeout_streak(self) -> None:
        client = SqliteClient()
        settings = config()

        def timeout_fetcher(url: str, timeout_seconds: float) -> str:
            raise CrawlTimeoutError("temporary timeout")

        for _ in range(2):
            result = CrawlCycle(
                target=get_target("dcinside-singularity"),
                config=settings,
                runtime=runtime(settings),
                client=client,
                fetcher=timeout_fetcher,
                cycle_started_at=FIXED_NOW,
                mode=CYCLE_MODE_HOT,
            ).run()
            self.assertEqual(result["status"], "partial")

        clock = FakeClock()
        backfill_runtime = CycleRuntime(
            min_request_interval_seconds=settings.min_request_interval_seconds,
            total_seconds=settings.cycle_max_seconds,
            hot_seconds=settings.hot_max_seconds,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        clock.advance(settings.cycle_max_seconds - 0.5)
        backfill_result = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=backfill_runtime,
            client=client,
            fetcher=MappingFetcher(
                {1: page_html(row(1000, "2026-07-15 19:00:00"))}
            ),
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_BACKFILL,
        ).run()

        self.assertEqual(backfill_result["status"], "partial")
        self.assertEqual(
            source_state_metadata(client)[TIMEOUT_STREAK_METADATA_KEY],
            0,
        )

        for _ in range(2):
            result = CrawlCycle(
                target=get_target("dcinside-singularity"),
                config=settings,
                runtime=runtime(settings),
                client=client,
                fetcher=timeout_fetcher,
                cycle_started_at=FIXED_NOW,
                mode=CYCLE_MODE_BACKFILL,
            ).run()
            self.assertEqual(result["status"], "partial")

        hot_result = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(
                {1: page_html(row(1000, "2026-07-16 19:00:00"))}
            ),
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        ).run()

        self.assertEqual(hot_result["status"], "completed")
        self.assertEqual(
            source_state_metadata(client)[TIMEOUT_STREAK_METADATA_KEY],
            0,
        )

        after_reset = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=timeout_fetcher,
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        ).run()
        self.assertEqual(after_reset["status"], "partial")
        self.assertEqual(
            source_state_metadata(client)[TIMEOUT_STREAK_METADATA_KEY],
            1,
        )

    def test_mixed_transient_and_blocked_failures_do_not_change_timeout_streak(self) -> None:
        client = SqliteClient()
        settings = config()

        def timeout_fetcher(url: str, timeout_seconds: float) -> str:
            raise CrawlTimeoutError("temporary timeout")

        for _ in range(2):
            CrawlCycle(
                target=get_target("dcinside-singularity"),
                config=settings,
                runtime=runtime(settings),
                client=client,
                fetcher=timeout_fetcher,
                cycle_started_at=FIXED_NOW,
                mode=CYCLE_MODE_HOT,
            ).run()

        errors = [
            CrawlTransientError("connection reset"),
            CrawlTimeoutError("temporary timeout"),
        ]

        def mixed_fetcher(url: str, timeout_seconds: float) -> str:
            raise errors.pop(0)

        generic_result = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=mixed_fetcher,
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        ).run()

        self.assertEqual(generic_result["status"], "failed")
        self.assertEqual(
            source_state_metadata(client)[TIMEOUT_STREAK_METADATA_KEY],
            2,
        )

        blocked_result = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(
                {1: page_html(row(1000, "2026-07-16 20:55:00"))},
                blocked_page=1,
            ),
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        ).run()

        self.assertEqual(blocked_result["status"], "blocked")
        self.assertEqual(
            source_state_metadata(client)[TIMEOUT_STREAK_METADATA_KEY],
            2,
        )

    def test_required_timeout_streak_writes_fail_the_cycle(self) -> None:
        client = FailingSourceStateWriteClient()
        settings = config()

        def timeout_fetcher(url: str, timeout_seconds: float) -> str:
            raise CrawlTimeoutError("temporary timeout")

        increment_cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=timeout_fetcher,
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        )
        client.fail_source_state_writes = True

        increment_result = increment_cycle.run()

        self.assertEqual(increment_result["status"], "failed")
        self.assertIn("persist consecutive timeout state", increment_result["error"])
        self.assertNotIn(TIMEOUT_STREAK_METADATA_KEY, source_state_metadata(client))

        client.fail_source_state_writes = False
        for _ in range(2):
            CrawlCycle(
                target=get_target("dcinside-singularity"),
                config=settings,
                runtime=runtime(settings),
                client=client,
                fetcher=timeout_fetcher,
                cycle_started_at=FIXED_NOW,
                mode=CYCLE_MODE_HOT,
            ).run()

        reset_cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(
                {1: page_html(row(1000, "2026-07-16 19:00:00"))}
            ),
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        )
        client.fail_source_state_writes = True

        reset_result = reset_cycle.run()

        self.assertEqual(reset_result["status"], "failed")
        self.assertIn("reset consecutive timeout state", reset_result["error"])
        self.assertEqual(
            source_state_metadata(client)[TIMEOUT_STREAK_METADATA_KEY],
            2,
        )

    def test_live_style_adjacent_id_swap_remains_fetchable(self) -> None:
        pages = {
            1: page_html(
                row(109, "2026-07-16 20:59:09"),
                row(107, "2026-07-16 20:59:07"),
                row(108, "2026-07-16 20:59:08"),
                row(106, "2026-07-16 20:59:06"),
            )
        }
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=MappingFetcher(pages),
            cycle_started_at=FIXED_NOW,
        )

        snapshot = cycle._fetch_page(1, HOT_PHASE)

        self.assertEqual(
            [post.external_post_id for post in snapshot.posts],
            ["109", "107", "108", "106"],
        )

    def test_blank_title_post_remains_fetchable_with_valid_id_and_link(self) -> None:
        pages = {
            1: page_html(
                row(
                    109,
                    "2026-07-16 20:59:09",
                    title_markup='<em class="icon_img icon_pic"></em>&nbsp;',
                ),
                row(108, "2026-07-16 20:59:08"),
            )
        }
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=MappingFetcher(pages),
            cycle_started_at=FIXED_NOW,
        )

        snapshot = cycle._fetch_page(1, HOT_PHASE)

        self.assertEqual(
            [(post.external_post_id, post.title) for post in snapshot.posts],
            [("109", ""), ("108", "post 108")],
        )
        self.assertTrue(snapshot.coverage_ordered)

    def test_unknown_non_numeric_row_keeps_posts_but_blocks_coverage(self) -> None:
        unknown_row = """
        <tr class="ub-content" data-no="" data-type="">
          <td class="gall_num">-</td>
          <td class="gall_subject">새 보조 유형</td>
          <td class="gall_tit"><a href="/event/new-format">auxiliary</a></td>
          <td class="gall_count">-</td>
          <td class="gall_recommend">-</td>
        </tr>
        """
        pages = {
            1: page_html(
                row(110, "2026-07-16 20:59:00", upvotes=4),
                unknown_row,
                row(109, "2026-07-16 20:58:00", upvotes=4),
            )
        }
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=MappingFetcher(pages),
            cycle_started_at=FIXED_NOW,
        )

        snapshot = cycle._fetch_page(1, HOT_PHASE)

        self.assertEqual(
            [post.external_post_id for post in snapshot.posts],
            ["110", "109"],
        )
        self.assertFalse(snapshot.coverage_ordered)

    def test_unknown_non_numeric_row_cannot_stop_hot_scan_early(self) -> None:
        unknown_row = """
        <tr class="ub-content" data-no="" data-type="">
          <td class="gall_num">-</td>
          <td class="gall_subject">새 보조 유형</td>
          <td class="gall_tit"><a href="/event/new-format">auxiliary</a></td>
        </tr>
        """
        pages = {
            1: page_html(
                row(110, "2026-07-16 20:59:00"),
                unknown_row,
                row(109, "2026-07-16 19:00:00", upvotes=4),
            ),
            2: page_html(
                row(108, "2026-07-16 19:59:00"),
                row(107, "2026-07-16 19:58:00"),
            ),
        }
        fetcher = MappingFetcher(pages)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )

        summary = cycle._run_hot_scan()

        self.assertEqual(fetcher.requested_pages, [1, 2])
        self.assertTrue(summary.target_complete)
        self.assertEqual(summary.stop_reason, "lookback_reached")
        self.assertEqual(summary.matched_posts, 1)

    def test_manager_bump_is_collection_safe_but_not_coverage_ordered(self) -> None:
        pages = {
            1: page_html(
                row(110, "2026-07-16 20:59:00"),
                row(90, "2026-07-16 19:00:00", upvotes=4),
                row(109, "2026-07-16 20:58:00"),
            )
        }
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=MappingFetcher(pages),
            cycle_started_at=FIXED_NOW,
        )

        snapshot = cycle._fetch_page(1, HOT_PHASE)

        self.assertFalse(snapshot.coverage_ordered)
        self.assertEqual(
            [post.external_post_id for post in snapshot.posts],
            ["110", "90", "109"],
        )

    def test_manager_bump_cannot_stop_hot_scan_early(self) -> None:
        pages = {
            1: page_html(
                row(110, "2026-07-16 20:59:00"),
                row(90, "2026-07-16 19:00:00", upvotes=4),
                row(109, "2026-07-16 20:58:00"),
            ),
            2: page_html(
                row(108, "2026-07-16 19:59:00"),
                row(107, "2026-07-16 19:58:00"),
            ),
        }
        fetcher = MappingFetcher(pages)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )

        summary = cycle._run_hot_scan()

        self.assertEqual(fetcher.requested_pages, [1, 2])
        self.assertTrue(summary.target_complete)
        self.assertEqual(summary.stop_reason, "lookback_reached")
        self.assertEqual(summary.matched_posts, 1)

    def test_hot_stops_when_a_young_board_clamps_after_its_last_page(self) -> None:
        pages = {
            1: page_html(
                row(110, "2026-07-16 20:59:00", upvotes=4),
                row(109, "2026-07-16 20:58:00"),
            ),
            2: page_html(
                row(108, "2026-07-16 20:30:00", upvotes=4),
                row(107, "2026-07-16 20:29:00"),
            ),
        }
        fetcher = MappingFetcher(
            pages,
            rendered_pages={3: 2},
        )
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        )

        result = cycle.run()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(fetcher.requested_pages, [1, 2, 3])
        self.assertEqual(result["phases"][0]["scanned_pages"], 2)
        self.assertEqual(result["phases"][0]["scanned_posts"], 4)
        self.assertEqual(result["phases"][0]["matched_posts"], 2)
        self.assertTrue(result["phases"][0]["target_complete"])
        self.assertEqual(result["phases"][0]["stop_reason"], "feed_exhausted")

    def test_young_board_backfill_defers_until_posts_can_be_finalized(self) -> None:
        pages = {
            1: page_html(row(110, "2026-07-16 20:59:00")),
            2: page_html(row(100, "2026-07-16 20:50:00")),
            4: page_html(row(80, "2026-07-16 20:40:00")),
            8: page_html(row(40, "2026-07-16 20:30:00")),
            11: page_html(row(1, "2026-07-16 20:20:00")),
        }
        fetcher = MappingFetcher(
            pages,
            rendered_pages={16: 11},
        )
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_BACKFILL,
        )

        result = cycle.run()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(fetcher.requested_pages, [1, 2, 4, 8, 16])
        self.assertEqual(
            [phase["stop_reason"] for phase in result["phases"]],
            ["no_finalizable_posts", "deferred_no_finalizable_posts"],
        )
        self.assertTrue(result["phases"][0]["target_complete"])
        self.assertFalse(result["phases"][1]["target_complete"])
        self.assertEqual(cycle.coverage, [])
        self.assertEqual(cycle.coverage_absences, [])
        self.assertFalse(cycle.source_state.backfill_anchor_post_id)
        self.assertEqual(
            cycle.source_state.state_metadata["finalize_page_hint"],
            11,
        )

    def test_clamped_backfill_with_eligible_rows_still_fails_closed(self) -> None:
        pages = {
            1: page_html(row(110, "2026-07-16 20:59:00")),
            2: page_html(row(100, "2026-07-16 20:50:00")),
            4: page_html(row(80, "2026-07-16 20:40:00")),
            8: page_html(row(40, "2026-07-16 20:30:00")),
            11: page_html(row(1, "2026-07-15 20:00:00", upvotes=4)),
        }
        fetcher = MappingFetcher(
            pages,
            rendered_pages={16: 11},
        )
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_BACKFILL,
        )

        result = cycle.run()

        self.assertEqual(result["status"], "failed")
        self.assertIn(
            "Backfill pagination could not be safely verified",
            result["error"],
        )
        self.assertEqual(cycle.coverage, [])
        self.assertEqual(cycle.coverage_absences, [])
        self.assertFalse(cycle.source_state.backfill_anchor_post_id)

    def test_unordered_all_new_cutoff_probe_is_never_committed(self) -> None:
        pages = {
            1: page_html(
                row(110, "2026-07-16 20:59:00"),
                row(90, "2026-07-16 20:40:00"),
                row(109, "2026-07-16 20:58:00"),
            ),
            2: page_html(
                row(89, "2026-07-15 21:00:01"),
                row(88, "2026-07-15 20:59:59", upvotes=4),
            ),
        }
        fetcher = MappingFetcher(pages)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )

        summary = cycle._run_recent_finalization()

        self.assertEqual(fetcher.requested_pages, [1, 2])
        self.assertTrue(summary.target_complete)
        self.assertEqual(summary.committed_intervals, 1)
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in cycle.coverage],
            [(88, 88)],
        )

    def test_unordered_cutoff_page_with_eligible_rows_stays_open(self) -> None:
        client = SqliteClient()
        pages = {
            1: page_html(
                row(110, "2026-07-16 20:59:00"),
                row(90, "2026-07-15 20:59:00", upvotes=4),
                row(109, "2026-07-16 20:58:00"),
            )
        }
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(pages),
            cycle_started_at=FIXED_NOW,
        )

        summary = cycle._run_recent_finalization()

        self.assertEqual(summary.status, "partial")
        self.assertEqual(summary.stop_reason, "page_order_not_coverage_safe")
        self.assertEqual(cycle.coverage, [])
        self.assertEqual(
            [item["external_post_id"] for item in client.query(
                "SELECT external_post_id FROM posts ORDER BY external_post_id"
            )],
            ["90"],
        )
        self.assertEqual(client.query("SELECT * FROM coverage_intervals"), [])
        self.assertEqual(
            cycle.source_state.state_metadata["finalize_page_hint"], 1
        )

    def test_unordered_history_page_continues_to_next_ordered_page(self) -> None:
        pages = {
            2: page_html(
                row(110, "2026-07-15 20:50:00"),
                row(90, "2026-07-15 20:40:00", upvotes=4),
                row(109, "2026-07-15 20:49:00"),
            ),
            3: page_html(
                row(89, "2026-07-15 20:30:00", upvotes=4),
                row(88, "2026-07-15 20:20:00"),
            ),
        }
        fetcher = MappingFetcher(pages, last_page=3)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 111, 160),
        ]

        summary = cycle._run_historical_backfill()

        self.assertEqual(summary.status, "partial")
        self.assertEqual(summary.stop_reason, "unordered_final_page_checked")
        self.assertEqual(summary.scanned_pages, 2)
        self.assertEqual(fetcher.requested_pages, [2, 3])
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in cycle.coverage],
            [(111, 160), (88, 89)],
        )
        self.assertEqual(
            cycle.source_state.state_metadata["history_page_hint"], 3
        )
        self.assertEqual(
            cycle.source_state.state_metadata["history_order_unsafe_page"], 2
        )
        self.assertEqual(
            cycle.source_state.state_metadata["history_target_mode"], "gap"
        )
        self.assertEqual(cycle.source_state.backfill_anchor_post_id, "88")

    def test_unordered_continuation_stops_on_unsafe_cutoff_suffix(self) -> None:
        pages = {
            2: page_html(
                row(110, "2026-07-15 20:50:00"),
                row(90, "2026-07-15 20:40:00", upvotes=4),
                row(109, "2026-07-15 20:49:00"),
            ),
            3: page_html(
                row(89, "2026-07-15 20:30:00", upvotes=4),
                row(88, "2026-07-16 20:20:00"),
            ),
        }
        fetcher = MappingFetcher(pages, last_page=4)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 111, 160),
        ]

        summary = cycle._run_historical_backfill()

        self.assertEqual(
            summary.stop_reason,
            "history_page_eligibility_not_id_suffix",
        )
        self.assertEqual(fetcher.requested_pages, [2, 3])
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in cycle.coverage],
            [(111, 160)],
        )
        self.assertEqual(
            cycle.source_state.state_metadata["history_page_hint"], 3
        )

    def test_unordered_history_without_newer_coverage_stays_on_unsafe_page(self) -> None:
        pages = {
            1: page_html(row(160, "2026-07-16 20:59:00")),
            2: page_html(
                row(110, "2026-07-15 20:50:00"),
                row(90, "2026-07-15 20:40:00", upvotes=4),
                row(109, "2026-07-15 20:49:00"),
            ),
            3: page_html(
                row(89, "2026-07-15 20:30:00", upvotes=4),
                row(88, "2026-07-15 20:20:00"),
            ),
        }
        fetcher = MappingFetcher(pages, last_page=3)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle._fetch_page(1, HOT_PHASE)
        cycle.source_state.state_metadata["history_page_hint"] = 2

        first = cycle._run_historical_backfill()
        second = cycle._run_historical_backfill()

        self.assertEqual(first.stop_reason, "page_order_not_coverage_safe")
        self.assertEqual(second.stop_reason, "page_order_not_coverage_safe")
        self.assertEqual(fetcher.requested_pages, [1, 2, 2])
        self.assertEqual(cycle.coverage, [])
        self.assertEqual(
            cycle.source_state.state_metadata["history_page_hint"], 2
        )
        self.assertFalse(
            cycle.source_state.state_metadata.get(
                "history_frontier_exhausted", False
            )
        )

    def test_bounded_unordered_gap_is_not_mistaken_for_complete_history(self) -> None:
        pages = {
            1: page_html(row(160, "2026-07-16 20:59:00")),
            2: page_html(
                row(110, "2026-07-15 20:50:00"),
                row(90, "2026-07-15 20:40:00", upvotes=4),
                row(109, "2026-07-15 20:49:00"),
            ),
            3: page_html(
                row(89, "2026-07-15 20:30:00", upvotes=4),
                row(88, "2026-07-15 20:20:00"),
            ),
        }
        fetcher = MappingFetcher(pages, last_page=3)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 111, 160),
        ]
        cycle._fetch_page(1, HOT_PHASE)
        cycle.source_state.state_metadata["history_page_hint"] = 2

        first = cycle._run_historical_backfill()
        requests_before_second = len(fetcher.requested_pages)
        second = cycle._run_historical_backfill()

        self.assertEqual(
            first.stop_reason,
            "history_frontier_exhausted_with_unordered_gap",
        )
        self.assertNotEqual(second.stop_reason, "history_complete")
        self.assertGreater(len(fetcher.requested_pages), requests_before_second)
        self.assertIn(2, fetcher.requested_pages[requests_before_second:])

    def test_unordered_history_archives_rows_then_commits_ordered_page(self) -> None:
        client = SqliteClient()
        pages = {
            2: page_html(
                row(110, "2026-07-15 20:50:00"),
                row(90, "2026-07-15 20:40:00", upvotes=4),
                row(109, "2026-07-15 20:49:00"),
            ),
            3: page_html(
                row(89, "2026-07-15 20:30:00", upvotes=4),
                row(88, "2026-07-15 20:20:00"),
            ),
        }
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(pages, last_page=3),
            cycle_started_at=FIXED_NOW,
        )
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 111, 160),
        ]

        summary = cycle._run_historical_backfill()

        self.assertEqual(summary.matched_posts, 2)
        self.assertEqual(
            [item["external_post_id"] for item in client.query(
                "SELECT external_post_id FROM posts ORDER BY external_post_id"
            )],
            ["89", "90"],
        )
        self.assertEqual(
            [
                (item["oldest_post_id"], item["newest_post_id"])
                for item in client.query("SELECT * FROM coverage_intervals")
            ],
            [(88, 89)],
        )
        self.assertEqual(client.query("SELECT * FROM coverage_absences"), [])

    def test_unordered_recent_page_does_not_starve_history(self) -> None:
        pages = {
            1: page_html(
                row(110, "2026-07-16 20:59:00"),
                row(90, "2026-07-15 20:59:00"),
                row(109, "2026-07-16 20:58:00"),
            ),
            2: page_html(
                row(89, "2026-07-15 20:50:00", upvotes=4),
                row(88, "2026-07-15 20:40:00"),
            ),
        }
        fetcher = MappingFetcher(pages, last_page=2)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )

        recent, historical = cycle._run_backfill()

        self.assertEqual(recent.status, "partial")
        self.assertEqual(recent.stop_reason, "page_order_not_coverage_safe")
        self.assertGreater(historical.scanned_pages, 0)
        self.assertTrue(historical.target_complete)
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in cycle.coverage],
            [(88, 89)],
        )

    def test_unordered_observation_invalidates_absence_without_new_coverage(self) -> None:
        client = SqliteClient()
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(
                {
                    1: page_html(
                        row(110, "2026-07-16 20:59:00"),
                        row(90, "2026-07-16 20:40:00"),
                        row(109, "2026-07-16 20:58:00"),
                    )
                }
            ),
            cycle_started_at=FIXED_NOW,
        )
        evidence = CoverageAbsence(
            source_key="dcinside-singularity",
            post_id=90,
            newer_page=1,
            older_page=2,
            newer_boundary_post_id=91,
            older_boundary_post_id=89,
        )
        cycle.absence_repository.record(evidence)
        cycle.coverage_absences = [evidence]

        snapshot = cycle._fetch_page(1, HOT_PHASE)

        self.assertFalse(snapshot.coverage_ordered)
        self.assertEqual(cycle.coverage_absences, [])
        self.assertEqual(client.query("SELECT * FROM coverage_absences"), [])
        self.assertEqual(client.query("SELECT * FROM coverage_intervals"), [])

    def test_finalization_eligibility_must_be_an_id_suffix(self) -> None:
        cutoff = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)
        safe = [
            post(109, "2026-07-15T12:00:01+00:00"),
            post(108, "2026-07-15T12:00:00+00:00"),
            post(107, "2026-07-15T11:59:59+00:00"),
        ]
        scattered = [
            post(109, "2026-07-15T12:00:01+00:00"),
            post(108, "2026-07-15T11:59:59+00:00"),
            post(107, "2026-07-15T12:00:02+00:00"),
            post(106, "2026-07-15T11:59:58+00:00"),
        ]
        scattered_in_dom_swap_order = [
            post(109, "2026-07-15T12:00:01+00:00"),
            post(107, "2026-07-15T12:00:02+00:00"),
            post(108, "2026-07-15T11:59:59+00:00"),
            post(106, "2026-07-15T11:59:58+00:00"),
        ]

        self.assertTrue(finalization_eligibility_is_id_suffix(safe, cutoff))
        self.assertFalse(finalization_eligibility_is_id_suffix(scattered, cutoff))
        self.assertFalse(
            finalization_eligibility_is_id_suffix(
                scattered_in_dom_swap_order, cutoff
            )
        )

    def test_scattered_cutoff_rows_are_deferred_without_coverage(self) -> None:
        pages = {
            1: page_html(
                row(109, "2026-07-15 21:00:01"),
                row(108, "2026-07-15 20:59:59"),
                row(107, "2026-07-15 21:00:02"),
                row(106, "2026-07-15 20:59:58"),
            )
        }
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=MappingFetcher(pages),
            cycle_started_at=FIXED_NOW,
        )

        summary = cycle._run_recent_finalization()

        self.assertEqual(summary.status, "partial")
        self.assertEqual(
            summary.stop_reason, "cutoff_page_eligibility_not_id_suffix"
        )
        self.assertEqual(cycle.coverage, [])

    def test_timestamp_inversion_on_same_cutoff_side_can_commit(self) -> None:
        pages = {
            1: page_html(
                row(109, "2026-07-15 20:59:50"),
                row(108, "2026-07-15 20:59:56"),
                row(107, "2026-07-15 20:59:40"),
            )
        }
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=MappingFetcher(pages),
            cycle_started_at=FIXED_NOW,
        )

        summary = cycle._run_recent_finalization()

        self.assertTrue(summary.target_complete)
        self.assertEqual(summary.committed_intervals, 1)
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in cycle.coverage],
            [(107, 109)],
        )

    def test_historical_scattered_cutoff_writes_no_finalization_state(self) -> None:
        client = SqliteClient()
        pages = {
            2: page_html(
                row(109, "2026-07-15 21:00:01"),
                row(108, "2026-07-15 20:59:59"),
                row(107, "2026-07-15 21:00:02"),
                row(106, "2026-07-15 20:59:58"),
            )
        }
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(pages),
            cycle_started_at=FIXED_NOW,
        )

        summary = cycle._run_historical_backfill()

        self.assertEqual(summary.status, "partial")
        self.assertEqual(
            summary.stop_reason, "history_page_eligibility_not_id_suffix"
        )
        self.assertEqual(client.query("SELECT * FROM posts"), [])
        self.assertEqual(client.query("SELECT * FROM coverage_intervals"), [])
        self.assertEqual(client.query("SELECT * FROM coverage_absences"), [])

    def test_commit_api_rejects_scattered_cutoff_rows(self) -> None:
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=MappingFetcher({}),
            cycle_started_at=FIXED_NOW,
        )
        scattered = [
            post(109, "2026-07-15T12:00:01+00:00"),
            post(108, "2026-07-15T11:59:59+00:00"),
            post(107, "2026-07-15T12:00:02+00:00"),
            post(106, "2026-07-15T11:59:58+00:00"),
        ]

        with self.assertRaisesRegex(ValueError, "eligible posts to be an ID suffix"):
            cycle._commit_finalized_page(scattered)

    def test_observed_post_invalidates_persisted_absence_evidence(self) -> None:
        client = SqliteClient()
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(
                {1: page_html(row(105, "2026-07-16 20:59:00"))}
            ),
            cycle_started_at=FIXED_NOW,
        )
        evidence = CoverageAbsence(
            source_key="dcinside-singularity",
            post_id=105,
            newer_page=1,
            older_page=2,
            newer_boundary_post_id=106,
            older_boundary_post_id=104,
        )
        cycle.absence_repository.record(evidence)
        cycle.coverage_absences = [evidence]
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 104, 104),
            CoverageInterval("dcinside-singularity", 106, 106),
        ]

        cycle._fetch_page(1, HOT_PHASE)

        self.assertEqual(cycle.coverage_absences, [])
        self.assertEqual(client.query("SELECT * FROM coverage_absences"), [])
        self.assertEqual(
            select_history_target(
                normalize_effective_coverage(
                    cycle.coverage, cycle.coverage_absences
                ),
                prefer_gap=True,
            ),
            (105, "gap"),
        )

    def test_historical_reappearance_aborts_stale_target_selection(self) -> None:
        client = SqliteClient()
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(
                {2: page_html(row(105, "2026-07-15 20:00:00"))}
            ),
            cycle_started_at=FIXED_NOW,
        )
        evidence = CoverageAbsence(
            source_key="dcinside-singularity",
            post_id=105,
            newer_page=1,
            older_page=2,
            newer_boundary_post_id=106,
            older_boundary_post_id=104,
        )
        cycle.absence_repository.record(evidence)
        cycle.coverage_absences = [evidence]
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 104, 104),
            CoverageInterval("dcinside-singularity", 106, 106),
        ]
        cycle.source_state.state_metadata["history_page_hint"] = 2

        summary = cycle._run_historical_backfill()

        self.assertEqual(summary.status, "partial")
        self.assertEqual(summary.stop_reason, "absence_invalidated_reselect")
        self.assertFalse(summary.target_complete)
        self.assertEqual(cycle.coverage_absences, [])
        self.assertEqual(client.query("SELECT * FROM coverage_absences"), [])
        self.assertEqual(client.query("SELECT * FROM coverage_intervals"), [])

    def test_absence_delete_failure_is_fail_closed(self) -> None:
        client = FailingAbsenceDeleteClient()
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(
                {1: page_html(row(105, "2026-07-16 20:59:00"))}
            ),
            cycle_started_at=FIXED_NOW,
        )
        evidence = CoverageAbsence(
            source_key="dcinside-singularity",
            post_id=105,
            newer_page=1,
            older_page=2,
            newer_boundary_post_id=106,
            older_boundary_post_id=104,
        )
        cycle.absence_repository.record(evidence)
        cycle.coverage_absences = [evidence]

        with self.assertRaisesRegex(RuntimeError, "absence delete failure"):
            cycle._fetch_page(1, HOT_PHASE)

        self.assertEqual([item.post_id for item in cycle.coverage_absences], [105])
        self.assertEqual(
            [row["post_id"] for row in client.query("SELECT post_id FROM coverage_absences")],
            [105],
        )
        self.assertEqual(client.query("SELECT * FROM coverage_intervals"), [])

    def test_time_windows_allow_more_than_legacy_nine_requests(self) -> None:
        page_times = {
            1: "2026-07-16 20:55:00",
            2: "2026-07-16 20:30:00",
            3: "2026-07-16 19:50:00",
            4: "2026-07-16 18:00:00",
            5: "2026-07-15 21:10:00",
            6: "2026-07-15 20:50:00",
            7: "2026-07-15 20:40:00",
            8: "2026-07-15 20:30:00",
            9: "2026-07-15 20:20:00",
            10: "2026-07-15 20:10:00",
            11: "2026-07-15 20:00:00",
            12: "2026-07-15 19:50:00",
        }
        pages = {
            page: page_html(row(2000 - page, created_at))
            for page, created_at in page_times.items()
        }
        fetcher = MappingFetcher(pages, last_page=12)
        settings = config()
        cycle_runtime = runtime(settings)

        result = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        ).run()

        phases = {item["run_type"]: item for item in result["phases"]}
        self.assertEqual(phases["hot_scan"]["pages"], [1, 2, 3])
        self.assertEqual(
            phases["finalize_recent"]["pages"],
            [1, 2, 4, 8, 6, 5],
        )
        self.assertEqual(
            phases["backfill_history"]["pages"],
            [7, 8, 9, 10, 11, 12],
        )
        self.assertGreater(cycle_runtime.request_count, 9)

    def test_hot_scan_and_both_backfill_lanes_receive_requests(self) -> None:
        pages = {
            1: page_html(
                row(1000, "2026-07-16 20:55:00"),
                row(999, "2026-07-16 20:30:00"),
            ),
            2: page_html(
                row(998, "2026-07-15 19:50:00", upvotes=4),
                row(997, "2026-07-15 19:40:00"),
            ),
            3: page_html(
                row(996, "2026-07-15 19:30:00", upvotes=4),
                row(995, "2026-07-15 19:20:00"),
            ),
        }
        fetcher = MappingFetcher(pages, last_page=3)
        settings = config()
        cycle_runtime = runtime(settings)
        result = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        ).run()

        self.assertIn(result["status"], {"completed", "partial"})
        phases = {item["run_type"]: item for item in result["phases"]}
        self.assertEqual(phases["hot_scan"]["pages"], [1, 2])
        self.assertGreater(phases["finalize_recent"]["scanned_pages"], 0)
        self.assertGreater(phases["backfill_history"]["scanned_pages"], 0)
        self.assertGreaterEqual(cycle_runtime.request_count, 4)

    def test_block_in_hot_scan_stops_the_complete_cycle(self) -> None:
        fetcher = MappingFetcher(
            {1: page_html(row(1000, "2026-07-16 20:55:00"))},
            blocked_page=1,
        )
        settings = config()
        cycle_runtime = runtime(settings)
        result = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        ).run()

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(cycle_runtime.request_count, 1)
        self.assertEqual(fetcher.requested_pages, [1])

    def test_active_block_cooldown_starts_no_source_request(self) -> None:
        fetcher = MappingFetcher(
            {1: page_html(row(1000, "2026-07-16 20:55:00"))}
        )
        settings = config()
        cycle_runtime = runtime(settings)
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.source_state.blocked_until = "2026-07-17T12:00:00+00:00"

        result = cycle.run()

        self.assertEqual(result["status"], "cooldown")
        self.assertEqual(cycle_runtime.request_count, 0)
        self.assertEqual(fetcher.requested_pages, [])

    def test_exact_24_hour_boundary_is_eligible(self) -> None:
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=MappingFetcher({}),
            cycle_started_at=FIXED_NOW,
        )
        eligible = cycle._eligible_posts(
            [
                post(3, "2026-07-15T12:00:01+00:00"),
                post(2, "2026-07-15T12:00:00+00:00"),
                post(1, "2026-07-15T11:59:59+00:00"),
            ]
        )
        self.assertEqual([item.external_post_id for item in eligible], ["2", "1"])

    def test_archive_is_written_before_coverage_and_only_for_matches(self) -> None:
        client = SqliteClient()
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher({}),
            cycle_started_at=FIXED_NOW,
        )
        cycle._commit_finalized_page(
            [
                post(100, "2026-07-15T10:00:00+00:00", upvotes=4),
                post(99, "2026-07-15T09:59:00+00:00", upvotes=0),
            ]
        )

        posts = client.query("SELECT external_post_id FROM posts ORDER BY external_post_id")
        coverage = client.query(
            "SELECT oldest_post_id, newest_post_id FROM coverage_intervals"
        )
        self.assertEqual(posts, [{"external_post_id": "100"}])
        self.assertEqual(coverage, [{"oldest_post_id": 99, "newest_post_id": 100}])

    def test_failed_archive_write_does_not_advance_coverage(self) -> None:
        client = FailingPostClient()
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher({}),
            cycle_started_at=FIXED_NOW,
        )

        with self.assertRaisesRegex(RuntimeError, "injected post write failure"):
            cycle._commit_finalized_page(
                [post(100, "2026-07-15T10:00:00+00:00", upvotes=4)]
            )

        coverage = client.query("SELECT * FROM coverage_intervals")
        self.assertEqual(coverage, [])

    def test_history_target_alternates_between_gap_and_old_frontier(self) -> None:
        intervals = [
            CoverageInterval("dcinside-singularity", 900, 950),
            CoverageInterval("dcinside-singularity", 800, 850),
        ]

        self.assertEqual(select_history_target(intervals, prefer_gap=True), (899, "gap"))
        self.assertEqual(
            select_history_target(intervals, prefer_gap=False),
            (799, "frontier"),
        )

    def test_separate_page_ranges_do_not_false_bridge_an_id_gap(self) -> None:
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=MappingFetcher({}),
            cycle_started_at=FIXED_NOW,
        )
        cycle._commit_finalized_page(
            [
                post(100, "2026-07-15T10:00:00+00:00"),
                post(95, "2026-07-15T09:55:00+00:00"),
            ]
        )
        cycle._commit_finalized_page(
            [
                post(90, "2026-07-15T09:50:00+00:00"),
                post(85, "2026-07-15T09:45:00+00:00"),
            ]
        )

        self.assertEqual(
            sorted((item.oldest_post_id, item.newest_post_id) for item in cycle.coverage),
            [(85, 90), (95, 100)],
        )

    def test_final_check_updates_an_existing_post_even_below_threshold(self) -> None:
        client = SqliteClient()
        target = get_target("dcinside-singularity")
        settings = config()
        CrawlCycle(
            target=target,
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher({}),
            cycle_started_at=FIXED_NOW,
        )
        initial = post(100, "2026-07-15T10:00:00+00:00", upvotes=4)
        upsert_posts(client, target, [initial.__dict__], FIXED_NOW.isoformat())
        final = post(100, "2026-07-15T10:00:00+00:00", upvotes=0)

        update_finalized_posts(
            client,
            target,
            [final.__dict__],
            "2026-07-16T12:00:00+00:00",
        )

        rows = client.query(
            "SELECT external_post_id, upvotes, qualifies_by FROM posts"
        )
        self.assertEqual(
            rows,
            [{"external_post_id": "100", "upvotes": 0, "qualifies_by": "none"}],
        )

    def test_stale_cutoff_hint_uses_bounded_search_without_oscillation(self) -> None:
        pages = {
            1: page_html(row(1100, "2026-07-16 20:00:00")),
            3: page_html(
                row(1000, "2026-07-16 01:00:00"),
                row(999, "2026-07-15 19:00:00"),
            ),
            5: page_html(row(900, "2026-07-15 18:00:00")),
            7: page_html(row(800, "2026-07-15 17:00:00")),
            8: page_html(row(700, "2026-07-15 16:00:00")),
        }
        fetcher = MappingFetcher(pages)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.source_state.state_metadata["finalize_page_hint"] = 8

        summary = cycle._run_recent_finalization()

        self.assertEqual(fetcher.requested_pages, [8, 7, 5, 1, 3])
        self.assertTrue(summary.target_complete)
        self.assertEqual(summary.stop_reason, "cutoff_page_checked")

    def test_backfill_mode_probes_fresh_head_before_using_saved_cutoff_hint(self) -> None:
        pages = {
            1: page_html(row(1100, "2026-07-16 20:00:00")),
            8: page_html(
                row(800, "2026-07-15 22:00:00"),
                row(799, "2026-07-15 20:00:00"),
            ),
        }
        fetcher = MappingFetcher(pages, last_page=8)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_BACKFILL,
        )
        cycle.source_state.state_metadata["finalize_page_hint"] = 8

        summary = cycle._run_recent_finalization()

        self.assertEqual(fetcher.requested_pages, [1, 8])
        self.assertEqual(cycle.observed_head_last_page, 8)
        self.assertEqual(summary.scanned_pages, 2)
        self.assertTrue(summary.target_complete)

    def test_backfill_mode_does_not_use_the_hot_request_lane(self) -> None:
        pages = {
            1: page_html(row(1100, "2026-07-16 20:00:00")),
            2: page_html(
                row(800, "2026-07-15 22:00:00"),
                row(799, "2026-07-15 20:00:00"),
            ),
        }
        fetcher = MappingFetcher(pages, last_page=2)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_BACKFILL,
        )

        result = cycle.run()

        self.assertEqual(result["mode"], CYCLE_MODE_BACKFILL)
        self.assertEqual(
            [phase["run_type"] for phase in result["phases"]],
            ["finalize_recent", "backfill_history"],
        )
        self.assertEqual(cycle.runtime.phase_request_count(HOT_PHASE), 0)

    def test_cutoff_between_pages_returns_cached_older_snapshot(self) -> None:
        pages = {
            1: page_html(row(1100, "2026-07-16 20:00:00")),
            2: page_html(row(1000, "2026-07-15 19:00:00")),
        }
        fetcher = MappingFetcher(pages)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.source_state.state_metadata["finalize_page_hint"] = 2

        summary = cycle._run_recent_finalization()

        self.assertEqual(fetcher.requested_pages, [2, 1])
        self.assertTrue(summary.target_complete)
        self.assertEqual(summary.stop_reason, "cutoff_page_checked")
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in cycle.coverage],
            [(1000, 1000)],
        )

    def test_missing_history_target_uses_older_page_without_false_bridge(self) -> None:
        pages = {
            1: page_html(
                row(110, "2026-07-15 10:00:00"),
                row(100, "2026-07-15 09:50:00"),
            ),
            2: page_html(
                row(98, "2026-07-15 09:40:00"),
                row(90, "2026-07-15 09:30:00"),
            ),
        }
        fetcher = MappingFetcher(pages)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 100, 110),
            CoverageInterval("dcinside-singularity", 70, 80),
        ]
        cycle.source_state.state_metadata["history_page_hint"] = 1

        summary = cycle._run_historical_backfill()

        self.assertEqual(fetcher.requested_pages, [1, 2, 1])
        self.assertTrue(summary.target_complete)
        self.assertEqual(
            sorted((item.oldest_post_id, item.newest_post_id) for item in cycle.coverage),
            [(70, 80), (90, 98), (100, 110)],
        )
        self.assertEqual(cycle.source_state.state_metadata["history_gap_cursor"], 99)
        self.assertEqual([item.post_id for item in cycle.coverage_absences], [99])
        self.assertEqual(summary.confirmed_absences, 1)
        self.assertEqual(summary.stop_reason, "verified_absence_recorded")
        self.assertEqual(
            select_history_target(
                normalize_effective_coverage(
                    cycle.coverage,
                    cycle.coverage_absences,
                ),
                prefer_gap=True,
                after_gap_id=99,
            ),
            (89, "gap"),
        )

    def test_missing_history_target_requires_newer_then_older_fetch_order(self) -> None:
        pages = {
            1: page_html(
                row(110, "2026-07-15 10:00:00"),
                row(100, "2026-07-15 09:50:00"),
            ),
            2: page_html(
                row(98, "2026-07-15 09:40:00"),
                row(90, "2026-07-15 09:30:00"),
            ),
        }
        fetcher = MappingFetcher(pages)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 100, 110),
            CoverageInterval("dcinside-singularity", 70, 80),
        ]
        cycle.source_state.state_metadata["history_page_hint"] = 2

        summary = cycle._run_historical_backfill()

        self.assertEqual(fetcher.requested_pages, [2, 1])
        self.assertEqual(cycle.coverage_absences, [])
        self.assertFalse(summary.target_complete)
        self.assertEqual(summary.stop_reason, "absence_evidence_needs_recheck")
        self.assertEqual(cycle.source_state.state_metadata["history_page_hint"], 1)
        self.assertEqual(cycle.source_state.state_metadata["history_target_mode"], "gap")

    def test_absence_recheck_deadline_keeps_gap_open(self) -> None:
        pages = {
            1: page_html(
                row(110, "2026-07-15 10:00:00"),
                row(100, "2026-07-15 09:50:00"),
            ),
            2: page_html(
                row(98, "2026-07-15 09:40:00"),
                row(90, "2026-07-15 09:30:00"),
            ),
        }
        clock = FakeClock()
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=5,
            cycle_max_seconds=61,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=5,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=0.001,
            total_seconds=61,
            hot_seconds=5,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        fetcher = TimedFetcher(pages, clock)
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 100, 110),
            CoverageInterval("dcinside-singularity", 70, 80),
        ]
        cycle.source_state.state_metadata["history_page_hint"] = 1

        summary = cycle._run_historical_backfill()

        self.assertEqual(fetcher.requested_pages, [1, 2])
        self.assertEqual(summary.status, "partial")
        self.assertEqual(summary.stop_reason, "request_timeout_budget")
        self.assertEqual(cycle.coverage_absences, [])
        self.assertEqual(cycle.source_state.state_metadata["history_page_hint"], 1)

    def test_deletion_shift_cannot_be_misclassified_as_an_absence(self) -> None:
        responses = {
            1: [
                page_html(
                    row(110, "2026-07-15 10:00:00"),
                    row(100, "2026-07-15 09:50:00"),
                ),
                # Post 110 disappears between requests. Live post 99 moves
                # from page 2 into the already-read page 1.
                page_html(
                    row(100, "2026-07-15 09:50:00"),
                    row(99, "2026-07-15 09:45:00"),
                ),
            ],
            2: [
                page_html(
                    row(98, "2026-07-15 09:40:00"),
                    row(90, "2026-07-15 09:30:00"),
                )
            ],
        }
        request_counts = {1: 0, 2: 0}
        requested_pages = []

        def shifting_fetcher(url: str, timeout_seconds: float) -> str:
            match = re.search(r"[?&]page=(\d+)", url)
            page = int(match.group(1)) if match else 1
            requested_pages.append(page)
            index = min(request_counts[page], len(responses[page]) - 1)
            request_counts[page] += 1
            return responses[page][index] + pagination_html(page, 100000)

        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=shifting_fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 100, 110),
            CoverageInterval("dcinside-singularity", 70, 80),
        ]
        cycle.source_state.state_metadata["history_page_hint"] = 1

        summary = cycle._run_historical_backfill()

        self.assertEqual(requested_pages, [1, 2, 1])
        self.assertEqual(cycle.coverage_absences, [])
        self.assertFalse(summary.target_complete)
        self.assertEqual(summary.stop_reason, "absence_evidence_needs_recheck")
        self.assertEqual(cycle.source_state.state_metadata["history_page_hint"], 1)

    def test_absence_write_failure_never_creates_effective_coverage(self) -> None:
        pages = {
            1: page_html(
                row(110, "2026-07-15 10:00:00"),
                row(100, "2026-07-15 09:50:00"),
            ),
            2: page_html(
                row(98, "2026-07-15 09:40:00"),
                row(90, "2026-07-15 09:30:00"),
            ),
        }
        client = FailingAbsenceClient()
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(pages),
            cycle_started_at=FIXED_NOW,
        )
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 100, 110),
            CoverageInterval("dcinside-singularity", 70, 80),
        ]
        cycle.source_state.state_metadata["history_page_hint"] = 1

        with self.assertRaisesRegex(RuntimeError, "absence write failure"):
            cycle._run_historical_backfill()

        self.assertEqual(cycle.coverage_absences, [])
        self.assertEqual(client.query("SELECT * FROM coverage_absences"), [])
        self.assertEqual(
            select_history_target(
                normalize_effective_coverage(
                    cycle.coverage,
                    cycle.coverage_absences,
                ),
                prefer_gap=True,
            ),
            (99, "gap"),
        )

    def test_confirmed_absence_closes_final_gap_without_more_requests(self) -> None:
        fetcher = MappingFetcher({})
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 1, 98),
            CoverageInterval("dcinside-singularity", 100, 110),
        ]
        cycle.coverage_absences = [
            CoverageAbsence(
                source_key="dcinside-singularity",
                post_id=99,
                newer_page=1,
                older_page=2,
                newer_boundary_post_id=100,
                older_boundary_post_id=98,
            )
        ]
        cycle.source_state.state_metadata["history_frontier_exhausted"] = True

        summary = cycle._run_historical_backfill()

        self.assertTrue(summary.target_complete)
        self.assertEqual(summary.stop_reason, "history_complete")
        self.assertEqual(fetcher.requested_pages, [])

    def test_cutoff_search_deadline_persists_next_probe(self) -> None:
        pages = {
            page: page_html(row(2000 - page, "2026-07-16 01:00:00"))
            for page in (1, 2, 4, 8, 16)
        }
        clock = FakeClock()
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=20,
            cycle_max_seconds=180,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=30,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=0.001,
            total_seconds=180,
            hot_seconds=20,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        fetcher = TimedFetcher(pages, clock)
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )

        summary = cycle._run_recent_finalization()

        self.assertEqual(fetcher.requested_pages, [1, 2, 4, 8, 16])
        self.assertEqual(summary.stop_reason, "deep_time_reservation")
        self.assertEqual(cycle.source_state.state_metadata["finalize_page_hint"], 32)

    def test_bootstrap_deadline_advances_history_hint(self) -> None:
        clock = FakeClock()
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=5,
            cycle_max_seconds=31,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=5,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=0.001,
            total_seconds=31,
            hot_seconds=5,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        fetcher = TimedFetcher(
            {2: page_html(row(1000, "2026-07-16 01:00:00"))},
            clock,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.source_state.state_metadata["finalize_page_hint"] = 1

        summary = cycle._run_historical_backfill()

        self.assertEqual(fetcher.requested_pages, [2])
        self.assertFalse(summary.target_complete)
        self.assertEqual(summary.status, "partial")
        self.assertEqual(summary.stop_reason, "request_timeout_budget")
        self.assertEqual(cycle.source_state.state_metadata["history_page_hint"], 3)

    def test_recent_request_timeout_cannot_consume_reserved_history_time(self) -> None:
        clock = FakeClock()
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=20,
            cycle_max_seconds=100,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=30,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=0.001,
            total_seconds=100,
            hot_seconds=20,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        fetcher = TimedFetcher(
            {1: page_html(row(1000, "2026-07-15 19:00:00"))},
            clock,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        clock.advance(65)

        summary = cycle._run_recent_finalization()

        self.assertEqual(fetcher.timeouts, [5.0])
        self.assertEqual(summary.stop_reason, "deep_time_reservation")
        self.assertEqual(cycle_runtime.remaining_seconds(), 30.0)
        self.assertEqual(cycle.coverage, [])

    def test_reserved_window_guard_does_not_count_unstarted_request(self) -> None:
        clock = FakeClock()
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=20,
            cycle_max_seconds=100,
            min_request_interval_seconds=20,
            deep_reserved_seconds=30,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=20,
            total_seconds=100,
            hot_seconds=20,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        fetcher = MappingFetcher(
            {1: page_html(row(1000, "2026-07-15 19:00:00"))}
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        clock.advance(49)
        cycle_runtime.acquire_request(BACKFILL_PHASE)
        cycle_runtime.complete_request()

        summary = cycle._run_recent_finalization()

        self.assertEqual(summary.status, "partial")
        self.assertEqual(summary.stop_reason, "deep_time_reservation")
        self.assertEqual(cycle_runtime.request_count, 1)
        self.assertEqual(fetcher.requested_pages, [])

    def test_backfill_rejects_clamped_page_before_coverage_can_advance(self) -> None:
        fetcher = MappingFetcher(
            {2: page_html(row(1000, "2026-07-15 10:00:00"))},
            rendered_pages={2: 1},
        )
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )

        with self.assertRaisesRegex(
            CrawlSourceError,
            "Backfill pagination could not be safely verified",
        ):
            cycle._fetch_page(2, BACKFILL_PHASE)

        self.assertEqual(cycle.coverage, [])

    def test_hot_page_remains_usable_without_navigation_evidence(self) -> None:
        fetcher = MappingFetcher(
            {1: page_html(row(1000, "2026-07-16 20:00:00"))},
            include_navigation=False,
        )
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )

        snapshot = cycle._fetch_page(1, HOT_PHASE)

        self.assertEqual([post.external_post_id for post in snapshot.posts], ["1000"])
        self.assertFalse(snapshot.navigation_valid)

    def test_history_exhaustion_accepts_fresh_final_page_when_head_estimate_differs(self) -> None:
        fetcher = MappingFetcher(
            {
                1: page_html(row(100, "2026-07-16 20:00:00")),
                2: page_html(row(90, "2026-07-15 10:00:00")),
            },
            last_page=2,
            last_pages={1: 3, 2: 2},
        )
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )

        cycle._fetch_page(1, HOT_PHASE)
        cycle.source_state.state_metadata["finalize_page_hint"] = 1
        first = cycle._run_historical_backfill()
        request_count = cycle.runtime.request_count
        second = cycle._run_historical_backfill()

        self.assertEqual(first.stop_reason, "history_frontier_exhausted")
        self.assertTrue(
            cycle.source_state.state_metadata["history_frontier_exhausted"]
        )
        self.assertEqual(cycle.source_state.state_metadata["history_last_page"], 2)
        self.assertEqual(second.stop_reason, "history_complete")
        self.assertEqual(cycle.runtime.request_count, request_count)
        self.assertEqual(fetcher.requested_pages, [1, 2])

    def test_head_without_end_link_cannot_prove_history_exhaustion(self) -> None:
        page_one_navigation = (
            '<div class="bottom_paging_box"><em>1</em>'
            '<a class="page_next" '
            'href="/mgallery/board/lists/?id=thesingularity&amp;page=2">'
            'next</a></div>'
        )
        fetcher = MappingFetcher(
            {
                1: page_html(row(100, "2026-07-16 20:00:00"))
                + page_one_navigation,
                2: page_html(row(90, "2026-07-15 10:00:00"))
                + pagination_html(2, 2),
            },
            include_navigation=False,
        )
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )

        head = cycle._fetch_page(1, HOT_PHASE)
        final = cycle._fetch_page(2, BACKFILL_PHASE)
        committed = cycle._commit_finalized_page(final.posts)

        self.assertTrue(head.navigation_valid)
        self.assertIsNone(cycle.observed_head_last_page)
        self.assertIsNotNone(committed)
        self.assertFalse(cycle._mark_history_frontier_exhausted(final, committed))

    def test_final_page_can_restore_a_lost_exhaustion_marker(self) -> None:
        pages = {
            1: page_html(row(110, "2026-07-16 20:00:00")),
            2: page_html(
                row(98, "2026-07-15 10:00:00"),
                row(50, "2026-07-15 09:00:00"),
            ),
        }
        fetcher = MappingFetcher(pages, last_page=2)
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=fetcher,
            cycle_started_at=FIXED_NOW,
        )
        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 50, 98),
        ]
        cycle.source_state.state_metadata["history_page_hint"] = 2

        cycle._fetch_page(1, HOT_PHASE)
        summary = cycle._run_historical_backfill()

        self.assertEqual(fetcher.requested_pages, [1, 2])
        self.assertEqual(summary.stop_reason, "history_frontier_exhausted")
        self.assertTrue(
            cycle.source_state.state_metadata["history_frontier_exhausted"]
        )

    def test_hot_persistence_defers_before_deep_time_reservation(self) -> None:
        clock = FakeClock()
        client = SqliteClient()
        client.timeout_seconds = 10
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=60,
            cycle_max_seconds=100,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=30,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=0.001,
            total_seconds=100,
            hot_seconds=60,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            client=client,
            fetcher=MappingFetcher(
                {1: page_html(row(1000, "2026-07-16 20:00:00", upvotes=4))}
            ),
            cycle_started_at=FIXED_NOW,
        )
        clock.advance(55)

        summary = cycle._run_hot_scan()

        self.assertEqual(summary.status, "partial")
        self.assertEqual(summary.stop_reason, "deep_time_reservation")
        self.assertEqual(client.query("SELECT * FROM posts"), [])
        self.assertEqual(client.query("SELECT * FROM crawl_runs"), [])

    def test_dedicated_hot_persistence_does_not_reserve_backfill_time(self) -> None:
        clock = FakeClock()
        client = SqliteClient()
        client.timeout_seconds = 10
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=60,
            cycle_max_seconds=100,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=30,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=0.001,
            total_seconds=100,
            hot_seconds=60,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            client=client,
            fetcher=MappingFetcher(
                {1: page_html(row(1000, "2026-07-16 20:00:00", upvotes=4))}
            ),
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        )
        clock.advance(55)

        summary = cycle._run_hot_scan()

        self.assertEqual(summary.status, "completed")
        self.assertEqual(summary.stop_reason, "lookback_reached")
        self.assertEqual(
            client.query("SELECT external_post_id FROM posts"),
            [{"external_post_id": "1000"}],
        )
        self.assertEqual(len(client.query("SELECT id FROM crawl_runs")), 1)

    def test_dedicated_hot_attempts_writes_when_preflight_cannot_fit(self) -> None:
        clock = FakeClock()
        client = SqliteClient()
        client.timeout_seconds = 10
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=20,
            cycle_max_seconds=20,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=0,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=0.001,
            total_seconds=20,
            hot_seconds=20,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        candidate_count = 29
        qualifying_rows = [
            row(1000 - index, "2026-07-16 20:00:00", upvotes=4)
            for index in range(candidate_count)
        ]
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            client=client,
            fetcher=MappingFetcher({1: page_html(*qualifying_rows)}),
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        )

        self.assertFalse(cycle._persistence_fits(query_count=6))

        summary = cycle._run_hot_scan()

        self.assertEqual(summary.status, "completed")
        self.assertEqual(summary.stop_reason, "lookback_reached")
        self.assertEqual(summary.hot_persisted_posts, candidate_count)
        self.assertEqual(len(client.query("SELECT id FROM posts")), candidate_count)
        self.assertEqual(len(client.query("SELECT id FROM crawl_runs")), 1)

    def test_dedicated_hot_persists_all_candidates_after_source_deadline(self) -> None:
        clock = FakeClock()
        client = SqliteClient()
        candidate_count = 29
        candidate_rows = [
            row(
                3000 - index,
                "2026-07-16 20:30:00",
                upvotes=4,
            )
            for index in range(candidate_count)
        ]
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=20,
            cycle_max_seconds=20,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=0,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=0.001,
            total_seconds=20,
            hot_seconds=20,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            client=client,
            fetcher=TimedFetcher({1: page_html(*candidate_rows)}, clock),
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        )

        summary = cycle._run_hot_scan()

        self.assertEqual(summary.status, "partial")
        self.assertEqual(summary.stop_reason, "request_timeout_budget")
        self.assertEqual(summary.matched_posts, candidate_count)
        self.assertEqual(summary.hot_persisted_posts, candidate_count)
        self.assertEqual(len(client.query("SELECT id FROM posts")), candidate_count)
        self.assertEqual(len(client.query("SELECT id FROM crawl_runs")), 1)

    def test_dedicated_hot_reports_successful_batches_before_later_failure(
        self,
    ) -> None:
        client = FailingSecondPostBatchClient()
        minutes = [(index * 7) % 15 for index in range(15)]
        candidate_rows = [
            row(
                4000 - index,
                f"2026-07-16 20:{minute:02}:00",
                upvotes=4,
            )
            for index, minute in enumerate(minutes)
        ]
        expected_first_batch = {
            str(4000 - index)
            for index, _minute in sorted(
                enumerate(minutes),
                key=lambda item: (item[1], 4000 - item[0]),
                reverse=True,
            )[:7]
        }
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=180,
            cycle_max_seconds=180,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=0,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher({1: page_html(*candidate_rows)}),
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        )

        result = cycle.run()

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phases"][0]["hot_persisted_posts"], 7)
        persisted_ids = {
            item["external_post_id"]
            for item in client.query("SELECT external_post_id FROM posts")
        }
        self.assertEqual(persisted_ids, expected_first_batch)
        self.assertEqual(
            client.query("SELECT status, run_type FROM crawl_runs"),
            [{"status": "failed", "run_type": "crawl_cycle"}],
        )

    def test_full_cycle_keeps_scan_order_when_later_hot_batch_fails(self) -> None:
        client = FailingSecondPostBatchClient()
        minutes = [(index * 7) % 15 for index in range(15)]
        candidate_rows = [
            row(
                5000 - index,
                f"2026-07-16 20:{minute:02}:00",
                upvotes=4,
            )
            for index, minute in enumerate(minutes)
        ]
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=180,
            cycle_max_seconds=240,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=30,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher({1: page_html(*candidate_rows)}),
            cycle_started_at=FIXED_NOW,
        )

        result = cycle.run()

        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["phases"][0]["hot_persisted_posts"])
        persisted_ids = {
            item["external_post_id"]
            for item in client.query("SELECT external_post_id FROM posts")
        }
        self.assertEqual(
            persisted_ids,
            {str(post_id) for post_id in range(5000, 4993, -1)},
        )

    def test_dedicated_hot_post_write_failure_is_not_a_green_partial(self) -> None:
        client = FailingPostClient()
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=180,
            cycle_max_seconds=180,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=0,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher(
                {1: page_html(row(1000, "2026-07-16 20:00:00", upvotes=4))}
            ),
            cycle_started_at=FIXED_NOW,
            mode=CYCLE_MODE_HOT,
        )

        result = cycle.run()

        self.assertEqual(result["status"], "failed")
        self.assertTrue(status_requires_failure_exit(result["status"]))
        self.assertEqual(client.query("SELECT * FROM posts"), [])
        self.assertEqual(
            client.query("SELECT status, run_type FROM crawl_runs"),
            [{"status": "failed", "run_type": "crawl_cycle"}],
        )

    def test_finalization_budget_counts_only_connected_coverage_cleanup(self) -> None:
        settings = config()
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=runtime(settings),
            fetcher=MappingFetcher({}),
            cycle_started_at=FIXED_NOW,
        )
        eligible = [
            post(post_id, "2026-07-15T10:00:00+00:00")
            for post_id in range(1000, 950, -1)
        ]
        cycle.coverage = [
            CoverageInterval(
                "dcinside-singularity",
                10000 + index * 100,
                10010 + index * 100,
            )
            for index in range(44)
        ]

        # 1 existing-ID lookup + 8 bounded upserts + 1 coverage INSERT.
        self.assertEqual(cycle._finalization_query_count(eligible), 10)

        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 900, 950),
            CoverageInterval("dcinside-singularity", 1001, 1050),
        ]
        # The new range bridges both intervals: one INSERT and two DELETEs.
        self.assertEqual(cycle._finalization_query_count(eligible), 12)

        cycle.coverage = [
            CoverageInterval("dcinside-singularity", 900, 1050),
        ]
        self.assertEqual(cycle._finalization_query_count(eligible), 0)

    def test_history_persistence_stops_without_coverage_at_cycle_budget(self) -> None:
        clock = FakeClock()
        client = SqliteClient()
        client.timeout_seconds = 10
        settings = CycleConfig(
            finalization_age_hours=24,
            hot_lookback_minutes=60,
            hot_max_seconds=20,
            cycle_max_seconds=100,
            min_request_interval_seconds=0.001,
            deep_reserved_seconds=30,
        )
        cycle_runtime = CycleRuntime(
            min_request_interval_seconds=0.001,
            total_seconds=100,
            hot_seconds=20,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        cycle = CrawlCycle(
            target=get_target("dcinside-singularity"),
            config=settings,
            runtime=cycle_runtime,
            client=client,
            fetcher=MappingFetcher(
                {2: page_html(row(1000, "2026-07-15 10:00:00", upvotes=4))}
            ),
            cycle_started_at=FIXED_NOW,
        )
        clock.advance(75)

        summary = cycle._run_historical_backfill()

        self.assertEqual(summary.status, "partial")
        self.assertEqual(summary.stop_reason, "cycle_persistence_budget")
        self.assertEqual(cycle.source_state.state_metadata["history_page_hint"], 2)
        self.assertEqual(cycle.coverage, [])
        self.assertEqual(client.query("SELECT * FROM coverage_intervals"), [])
        self.assertEqual(client.query("SELECT * FROM posts"), [])

    def test_block_cooldown_uses_detection_time_and_run_log_fallback(self) -> None:
        client = SqliteClient()
        settings = config()
        target = get_target("dcinside-singularity")
        first = CrawlCycle(
            target=target,
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=MappingFetcher({}),
            cycle_started_at=FIXED_NOW,
        )
        detected_at = "2026-07-16T12:20:00+00:00"
        with patch("crawler.jobs.run_cycle.utc_now", return_value=detected_at), patch(
            "crawler.jobs.scan_new_posts.utc_now",
            return_value=detected_at,
        ):
            first._record_block("HTTP 429", [])

        self.assertEqual(
            first.source_state.blocked_until,
            "2026-07-16T18:20:00+00:00",
        )

        # Simulate a source_state cooldown write that was unavailable while
        # the blocked crawl_runs record did persist.
        client.query(
            "UPDATE source_state SET blocked_until = '' WHERE source_key = ?",
            [target.key],
        )
        second_fetcher = MappingFetcher(
            {1: page_html(row(1000, "2026-07-16 20:00:00"))}
        )
        second = CrawlCycle(
            target=target,
            config=settings,
            runtime=runtime(settings),
            client=client,
            fetcher=second_fetcher,
            cycle_started_at=datetime(2026, 7, 16, 12, 30, tzinfo=timezone.utc),
        )

        result = second.run()

        self.assertEqual(result["status"], "cooldown")
        self.assertEqual(second_fetcher.requested_pages, [])


if __name__ == "__main__":
    unittest.main()
