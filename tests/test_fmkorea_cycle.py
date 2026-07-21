from __future__ import annotations

import html
import sqlite3
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from urllib import error
from urllib.parse import parse_qs, urlparse

from crawler.jobs.run_fmkorea_cycle import (
    CYCLE_MODE_BACKFILL,
    CYCLE_MODE_HOT,
    FmkoreaCycle,
    run_fmkorea_target,
)
from crawler.jobs.scan_new_posts import upsert_source
from crawler.runtime import CycleRuntime
from crawler.state import get_source_state, save_source_state
from crawler.targets import TargetBoard, get_target


NOW = datetime(2026, 7, 21, 15, 39, tzinfo=timezone.utc)


def search_row(
    document_id: int,
    *,
    date: str = "2026.07.22 00:20",
    upvotes: int = 0,
    comments: int = 0,
) -> str:
    return (
        f'<li class="li_best2" data-document-srl="{document_id}">'
        '<span class="category">축구</span>'
        '<h3 class="title">'
        f'<a href="/{document_id}"><span class="ellipsis-target">'
        f"뮌헨 {document_id}</span></a></h3>"
        '<a class="pc_voted_count" '
        f'href="/index.php?document_srl={document_id}">'
        f'<span class="count">{upvotes}</span></a>'
        f'<span class="comment_count">[{comments}]</span>'
        f'<span class="regdate">{date}</span>'
        "</li>"
    )


def board_row(
    document_id: int,
    *,
    upvotes: int,
    comments: int,
    date: str = "00:20",
) -> str:
    return (
        f'<tr data-document-srl="{document_id}">'
        '<td class="cate">바이에른</td>'
        '<td class="title">'
        f'<a class="hx" href="/{document_id}">글 {document_id}</a>'
        f'<a class="replyNum">[{comments}]</a></td>'
        f'<td class="time">{date}</td>'
        '<td class="m_no">9,999</td>'
        f'<td class="m_no m_no_voted">{upvotes}</td>'
        "</tr>"
    )


def pagination(target: TargetBoard, current: int, last: int) -> str:
    links = []
    for page in range(1, last + 1):
        if page == current:
            links.append(f"<strong>{page}</strong>")
        else:
            links.append(
                f'<a href="{html.escape(target.page_url(page), quote=True)}">'
                f"{page}</a>"
            )
    return '<div class="pagination">' + "".join(links) + "</div>"


def search_page(
    target: TargetBoard,
    page: int,
    last: int,
    *rows: str,
) -> str:
    return "<ul>" + "".join(rows) + "</ul>" + pagination(target, page, last)


def board_page(
    target: TargetBoard,
    page: int,
    last: int,
    *rows: str,
) -> str:
    return (
        "<table><tbody>"
        + "".join(rows)
        + "</tbody></table>"
        + pagination(target, page, last)
    )


class MappingFetcher:
    def __init__(self, pages: dict[int, str]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def __call__(self, url: str, timeout_seconds: float) -> str:
        self.urls.append(url)
        page = int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
        return self.pages[page]

    @property
    def requested_pages(self) -> list[int]:
        return [
            int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
            for url in self.urls
        ]


class SqliteClient:
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        schema = Path("cloudflare/schema.sql").read_text(encoding="utf-8")
        self.connection.executescript(schema)
        self.timeout_seconds = 1

    def query(self, sql: str, params=None):
        cursor = self.connection.execute(sql, params or [])
        self.connection.commit()
        if cursor.description is None:
            return []
        return [dict(row) for row in cursor.fetchall()]


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds

    def advance(self, seconds: float) -> None:
        self.value += seconds


class AdvancingFetcher(MappingFetcher):
    def __init__(self, pages: dict[int, str], clock: FakeClock) -> None:
        super().__init__(pages)
        self.clock = clock

    def __call__(self, url: str, timeout_seconds: float) -> str:
        result = super().__call__(url, timeout_seconds)
        self.clock.advance(2.5)
        return result


def target_with_limits(
    key: str,
    *,
    hot_pages: int = 3,
    backfill_pages: int = 3,
) -> TargetBoard:
    return replace(
        get_target(key),
        hot_max_pages=hot_pages,
        backfill_max_pages=backfill_pages,
        request_interval_seconds=0.001,
    )


def seed_hint(client: SqliteClient, target: TargetBoard, page: int) -> None:
    timestamp = NOW.isoformat()
    upsert_source(client, target, timestamp)
    state = get_source_state(client, target.key)
    assert state is not None
    state.backfill_page_hint = page
    save_source_state(client, state)


class FmkoreaHotCycleTests(unittest.TestCase):
    def test_search_uses_special_first_url_then_index_pages_and_collects_all(self) -> None:
        target = target_with_limits(
            "fmkorea-best-munich-search",
            hot_pages=3,
        )
        fetcher = MappingFetcher(
            {
                1: search_page(target, 1, 2, search_row(101)),
                2: search_page(
                    target,
                    2,
                    2,
                    search_row(100, date="2026.07.20"),
                ),
            }
        )

        result = run_fmkorea_target(
            target,
            CYCLE_MODE_HOT,
            fetcher=fetcher,
            now=NOW,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["phase"]["matched_posts"], 2)
        self.assertEqual(fetcher.requested_pages, [1, 2])
        self.assertIn("/search.php?", fetcher.urls[0])
        self.assertIn("/index.php?", fetcher.urls[1])

    def test_bayern_board_applies_weighted_threshold(self) -> None:
        target = target_with_limits("fmkorea-bayern-board", hot_pages=1)
        fetcher = MappingFetcher(
            {
                1: board_page(
                    target,
                    1,
                    1,
                    board_row(201, upvotes=14, comments=10),
                    board_row(200, upvotes=14, comments=9),
                )
            }
        )

        result = run_fmkorea_target(
            target,
            CYCLE_MODE_HOT,
            fetcher=fetcher,
            now=NOW,
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["phase"]["matched_posts"], 1)

    def test_hot_persists_safe_page_before_time_budget_stops_next_fetch(self) -> None:
        target = target_with_limits(
            "fmkorea-best-munich-search",
            hot_pages=3,
        )
        client = SqliteClient()
        clock = FakeClock()
        runtime = CycleRuntime(
            min_request_interval_seconds=0.001,
            total_seconds=3,
            hot_seconds=3,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )
        fetcher = AdvancingFetcher(
            {1: search_page(target, 1, 3, search_row(301))},
            clock,
        )

        result = FmkoreaCycle(
            target=target,
            mode=CYCLE_MODE_HOT,
            client=client,
            fetcher=fetcher,
            now=NOW,
            runtime=runtime,
        ).run()

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["phase"]["stop_reason"], "request_timeout_budget")
        self.assertEqual(result["phase"]["persisted_posts"], 1)
        self.assertEqual(
            client.query("SELECT external_post_id FROM posts"),
            [{"external_post_id": "301"}],
        )


class FmkoreaBackfillCycleTests(unittest.TestCase):
    def test_backfill_overlaps_saved_hint_and_advances_only_safe_pages(self) -> None:
        target = target_with_limits(
            "fmkorea-best-munich-search",
            backfill_pages=2,
        )
        client = SqliteClient()
        seed_hint(client, target, 4)
        fetcher = MappingFetcher(
            {
                3: search_page(target, 3, 5, search_row(403)),
                4: search_page(target, 4, 5, search_row(404)),
            }
        )

        result = run_fmkorea_target(
            target,
            CYCLE_MODE_BACKFILL,
            client=client,
            fetcher=fetcher,
            now=NOW,
        )

        state = get_source_state(client, target.key)
        assert state is not None
        self.assertEqual(result["status"], "partial")
        self.assertEqual(fetcher.requested_pages, [3, 4])
        self.assertEqual(state.backfill_page_hint, 5)

    def test_rendered_page_mismatch_fails_without_advancing_hint(self) -> None:
        target = target_with_limits(
            "fmkorea-best-munich-search",
            backfill_pages=1,
        )
        client = SqliteClient()
        seed_hint(client, target, 4)
        fetcher = MappingFetcher(
            {3: search_page(target, 2, 5, search_row(503))}
        )

        result = run_fmkorea_target(
            target,
            CYCLE_MODE_BACKFILL,
            client=client,
            fetcher=fetcher,
            now=NOW,
        )

        state = get_source_state(client, target.key)
        assert state is not None
        self.assertEqual(result["status"], "failed")
        self.assertEqual(state.backfill_page_hint, 4)
        self.assertEqual(client.query("SELECT id FROM posts"), [])

    def test_verified_last_page_starts_a_fresh_pass_next_backfill(self) -> None:
        target = target_with_limits(
            "fmkorea-best-munich-search",
            backfill_pages=3,
        )
        client = SqliteClient()
        fetcher = MappingFetcher(
            {
                1: search_page(target, 1, 2, search_row(601)),
                2: search_page(target, 2, 2, search_row(602)),
            }
        )

        first = run_fmkorea_target(
            target,
            CYCLE_MODE_BACKFILL,
            client=client,
            fetcher=fetcher,
            now=NOW,
        )
        # The former tail row may shift onto the new head between passes.  It
        # must not be mistaken for a repeated-page response from one pass.
        second_fetcher = MappingFetcher(
            {1: search_page(target, 1, 1, search_row(602))}
        )
        second = run_fmkorea_target(
            target,
            CYCLE_MODE_BACKFILL,
            client=client,
            fetcher=second_fetcher,
            now=NOW,
        )

        state = get_source_state(client, target.key)
        assert state is not None
        self.assertEqual(first["status"], "completed")
        self.assertTrue(state.state_metadata["backfill_complete"])
        self.assertEqual(second["status"], "completed")
        self.assertEqual(second_fetcher.requested_pages, [1])
        self.assertEqual(state.backfill_page_hint, 1)

    def test_repeated_row_fingerprint_does_not_advance_second_page(self) -> None:
        target = target_with_limits(
            "fmkorea-best-munich-search",
            backfill_pages=2,
        )
        client = SqliteClient()
        fetcher = MappingFetcher(
            {
                1: search_page(target, 1, 3, search_row(701)),
                2: search_page(target, 2, 3, search_row(701)),
            }
        )

        result = run_fmkorea_target(
            target,
            CYCLE_MODE_BACKFILL,
            client=client,
            fetcher=fetcher,
            now=NOW,
        )

        state = get_source_state(client, target.key)
        assert state is not None
        self.assertEqual(result["status"], "failed")
        self.assertEqual(state.backfill_page_hint, 2)

    def test_http_430_is_blocked_and_sets_cooldown(self) -> None:
        target = target_with_limits("fmkorea-bayern-board", backfill_pages=1)
        client = SqliteClient()

        def blocked(url: str, timeout_seconds: float) -> str:
            raise error.HTTPError(url, 430, "blocked", {}, None)

        result = run_fmkorea_target(
            target,
            CYCLE_MODE_BACKFILL,
            client=client,
            fetcher=blocked,
            now=NOW,
        )

        state = get_source_state(client, target.key)
        assert state is not None
        self.assertEqual(result["status"], "blocked")
        self.assertIn("HTTP 430", state.last_block_reason)
        self.assertGreater(
            datetime.fromisoformat(state.blocked_until),
            NOW,
        )


if __name__ == "__main__":
    unittest.main()
