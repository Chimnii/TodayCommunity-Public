from __future__ import annotations

import http.client
import sqlite3
import socket
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from urllib import error

from crawler.jobs.scan_new_posts import (
    CrawlBlockedError,
    CrawlTimeoutError,
    CrawlTransientError,
    EXISTING_POST_IDS_PER_QUERY,
    POSTS_PER_UPSERT,
    detect_blocked_html,
    existing_post_lookup_query_count,
    fetch_html,
    post_upsert_query_count,
    update_finalized_posts,
    upsert_posts,
    upsert_source,
)
from crawler.targets import get_target


class RecordingClient:
    def __init__(self) -> None:
        self.calls = []

    def query(self, sql, params=None):
        self.calls.append((sql, list(params or [])))
        return []


class SqliteClient:
    def __init__(self) -> None:
        schema_path = Path(__file__).resolve().parents[1] / "cloudflare" / "schema.sql"
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(schema_path.read_text(encoding="utf-8"))

    def query(self, sql, params=None):
        cursor = self.connection.execute(sql, list(params or []))
        if cursor.description is None:
            self.connection.commit()
            return []
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def sample_post(post_id: int) -> dict:
    return {
        "external_post_id": str(post_id),
        "post_url": f"https://example.com/{post_id}",
        "subject": "일반",
        "title": f"post {post_id}",
        "created_at": "2026-07-16T00:00:00+09:00",
        "created_at_raw": "2026-07-16 00:00:00",
        "upvotes": 4,
        "comments": 0,
        "qualifies_by": "upvotes",
    }


class BlockDetectionTests(unittest.TestCase):
    def test_normal_board_content_does_not_block_on_title_words(self) -> None:
        html = (
            '<tr class="ub-content" data-no="1">'
            "captcha access denied attention required"
            "</tr>"
        )

        self.assertEqual(detect_blocked_html(html), "")

    def test_challenge_dom_without_board_rows_is_detected(self) -> None:
        html = '<html><script src="/cdn-cgi/challenge-platform/test.js"></script></html>'

        self.assertEqual(detect_blocked_html(html), "Cloudflare browser challenge")


class FetchHtmlTests(unittest.TestCase):
    def test_supports_a_caller_owned_transport(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self) -> bytes:
                return b"<html>ok</html>"

        calls = []

        def open_url(http_request, *, timeout):
            calls.append((http_request.full_url, timeout))
            return Response()

        result = fetch_html(
            "https://example.com/list",
            5,
            open_url=open_url,
        )

        self.assertEqual(result, "<html>ok</html>")
        self.assertEqual(calls, [("https://example.com/list", 5)])

    def test_direct_transport_timeout_uses_timeout_subclass(self) -> None:
        with patch(
            "crawler.jobs.scan_new_posts.request.urlopen",
            side_effect=socket.timeout("timed out"),
        ):
            with self.assertRaises(CrawlTimeoutError):
                fetch_html("https://example.com/list", 5)

    def test_url_error_wrapping_transport_timeout_uses_timeout_subclass(self) -> None:
        with patch(
            "crawler.jobs.scan_new_posts.request.urlopen",
            side_effect=error.URLError(socket.timeout("timed out")),
        ):
            with self.assertRaises(CrawlTimeoutError):
                fetch_html("https://example.com/list", 5)

    def test_http_timeout_status_stays_generic_transient(self) -> None:
        http_error = error.HTTPError(
            "https://example.com/list",
            408,
            "Request Timeout",
            {},
            None,
        )

        with patch(
            "crawler.jobs.scan_new_posts.request.urlopen",
            side_effect=http_error,
        ):
            with self.assertRaises(CrawlTransientError) as raised:
                fetch_html("https://example.com/list", 5)

        self.assertNotIsInstance(raised.exception, CrawlTimeoutError)

    def test_non_timeout_url_errors_stay_generic_transient(self) -> None:
        reasons = (
            socket.gaierror("name resolution failed"),
            ConnectionResetError("connection reset"),
        )

        for reason in reasons:
            with self.subTest(error=type(reason).__name__):
                with patch(
                    "crawler.jobs.scan_new_posts.request.urlopen",
                    side_effect=error.URLError(reason),
                ):
                    with self.assertRaises(CrawlTransientError) as raised:
                        fetch_html("https://example.com/list", 5)

                self.assertNotIsInstance(raised.exception, CrawlTimeoutError)

    def test_interrupted_response_errors_are_generic_transient(self) -> None:
        errors = (
            http.client.RemoteDisconnected("remote closed the connection"),
            http.client.IncompleteRead(b"partial", 10),
            ConnectionResetError("connection reset"),
        )

        for transport_error in errors:
            with self.subTest(error=type(transport_error).__name__):
                with patch(
                    "crawler.jobs.scan_new_posts.request.urlopen",
                    side_effect=transport_error,
                ):
                    with self.assertRaises(CrawlTransientError) as raised:
                        fetch_html("https://example.com/list", 5)

                self.assertNotIsInstance(raised.exception, CrawlTimeoutError)

    def test_http_5xx_stays_generic_transient(self) -> None:
        http_error = error.HTTPError(
            "https://example.com/list",
            503,
            "Service Unavailable",
            {},
            None,
        )

        with patch(
            "crawler.jobs.scan_new_posts.request.urlopen",
            side_effect=http_error,
        ):
            with self.assertRaises(CrawlTransientError) as raised:
                fetch_html("https://example.com/list", 5)

        self.assertNotIsInstance(raised.exception, CrawlTimeoutError)

    def test_http_block_statuses_stay_blocked(self) -> None:
        for status in (403, 429, 430):
            with self.subTest(status=status):
                http_error = error.HTTPError(
                    "https://example.com/list",
                    status,
                    "Blocked",
                    {},
                    None,
                )
                with patch(
                    "crawler.jobs.scan_new_posts.request.urlopen",
                    side_effect=http_error,
                ):
                    with self.assertRaises(CrawlBlockedError):
                        fetch_html("https://example.com/list", 5)


class BatchedPostUpsertTests(unittest.TestCase):
    def test_finalizer_applies_combined_rule_only_to_new_posts(self) -> None:
        client = RecordingClient()
        posts = [
            {
                **sample_post(1),
                "upvotes": 0,
                "comments": 15,
                "qualifies_by": "none",
            },
            {
                **sample_post(2),
                "upvotes": 3,
                "comments": 5,
                "qualifies_by": "upvotes+comments",
            },
        ]

        update_finalized_posts(
            client,
            get_target("dcinside-singularity"),
            posts,
            "2026-07-16T00:00:00+00:00",
        )

        insert_calls = [
            (sql, params)
            for sql, params in client.calls
            if "INSERT INTO posts" in sql
        ]
        self.assertEqual(len(insert_calls), 1)
        self.assertIn("2", insert_calls[0][1])
        self.assertNotIn("1", insert_calls[0][1])

    def test_multi_row_upserts_stay_within_the_d1_parameter_limit(self) -> None:
        client = RecordingClient()
        posts = [sample_post(post_id) for post_id in range(1, POSTS_PER_UPSERT + 2)]

        upsert_posts(
            client,
            get_target("dcinside-singularity"),
            posts,
            "2026-07-16T00:00:00+00:00",
        )

        self.assertEqual(post_upsert_query_count(len(posts)), 2)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual([len(params) for _, params in client.calls], [99, 15])
        self.assertTrue(all("INSERT INTO posts" in sql for sql, _ in client.calls))

    def test_subject_is_inserted_but_not_backfilled_on_conflict(self) -> None:
        client = RecordingClient()

        upsert_posts(
            client,
            get_target("dcinside-singularity"),
            [sample_post(1)],
            "2026-07-16T00:00:00+00:00",
        )

        sql, params = client.calls[0]
        insert_clause, update_clause = sql.split(
            "ON CONFLICT DO UPDATE SET",
            1,
        )
        self.assertIn("subject", insert_clause)
        self.assertNotIn("subject", update_clause)
        self.assertIn("일반", params)

    def test_blank_title_is_persisted_with_canonical_identity(self) -> None:
        client = SqliteClient()
        target = get_target("dcinside-ai-utilize")
        checked_at = "2026-07-22T09:08:48+00:00"
        post_url = (
            "https://gall.dcinside.com/mgallery/board/view/"
            "?id=ai_utilize&no=7905&page=3"
        )
        upsert_source(client, target, checked_at)

        upsert_posts(
            client,
            target,
            [
                {
                    **sample_post(7905),
                    "post_url": post_url,
                    "title": "",
                }
            ],
            checked_at,
        )

        self.assertEqual(
            client.query(
                """
                SELECT canonical_post_key, external_post_id, post_url, title
                FROM posts
                """
            ),
            [
                {
                    "canonical_post_key": "dcinside:ai_utilize:7905",
                    "external_post_id": "7905",
                    "post_url": post_url,
                    "title": "",
                }
            ],
        )

    def test_migrated_board_preserves_same_numbered_legacy_post(self) -> None:
        client = SqliteClient()
        migrated = get_target("dcinside-ai-utilize")
        legacy = replace(
            migrated,
            key="dcinside-agent-stack",
            board_name="에이전트 스택(Agent Stack) 마이너 갤러리",
            board_url=(
                "https://gall.dcinside.com/mgallery/board/lists/?id=agent_stack"
            ),
            list_url_template=(
                "https://gall.dcinside.com/mgallery/board/lists/"
                "?id=agent_stack&page={page}"
            ),
            canonical_namespace="dcinside:agent_stack",
        )
        checked_at = "2026-07-23T04:00:00+00:00"
        upsert_source(client, legacy, checked_at)
        client.query(
            """
            UPDATE source_state
            SET backfill_anchor_post_id = ?,
                state_metadata = ?,
                updated_at = ?
            WHERE source_key = ?
            """,
            [
                "523",
                '{"history_page_hint":168}',
                checked_at,
                legacy.key,
            ],
        )
        client.query(
            """
            INSERT INTO coverage_intervals (
              source_key, oldest_post_id, newest_post_id, checked_at,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [legacy.key, 523, 625, checked_at, checked_at, checked_at],
        )
        client.query(
            """
            INSERT INTO crawl_runs (
              source_key, run_type, status, scanned_pages, scanned_posts,
              matched_posts, started_at, finished_at, error_message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                legacy.key,
                "backfill_history",
                "completed",
                10,
                500,
                20,
                checked_at,
                checked_at,
                "",
            ],
        )
        upsert_posts(
            client,
            legacy,
            [
                {
                    **sample_post(533),
                    "post_url": (
                        "https://gall.dcinside.com/mgallery/board/view/"
                        "?id=agent_stack&no=533"
                    ),
                    "title": "legacy post",
                }
            ],
            checked_at,
        )

        upsert_source(client, migrated, "2026-07-23T05:00:00+00:00")
        upsert_posts(
            client,
            migrated,
            [
                {
                    **sample_post(533),
                    "post_url": (
                        "https://gall.dcinside.com/mgallery/board/view/"
                        "?id=ai_utilize&no=533"
                    ),
                    "title": "migrated post",
                }
            ],
            "2026-07-23T05:00:00+00:00",
        )

        self.assertEqual(
            client.query(
                """
                SELECT source_key, archive_key, canonical_post_key,
                       external_post_id, title
                FROM posts
                ORDER BY source_key
                """
            ),
            [
                {
                    "source_key": "dcinside-agent-stack",
                    "archive_key": "dcinside-agent-stack",
                    "canonical_post_key": "dcinside:agent_stack:533",
                    "external_post_id": "533",
                    "title": "legacy post",
                },
                {
                    "source_key": "dcinside-ai-utilize",
                    "archive_key": "dcinside-agent-stack",
                    "canonical_post_key": "dcinside:ai_utilize:533",
                    "external_post_id": "533",
                    "title": "migrated post",
                },
            ],
        )
        self.assertEqual(
            client.query(
                """
                SELECT source_key, backfill_anchor_post_id, state_metadata
                FROM source_state
                WHERE source_key IN (?, ?)
                ORDER BY source_key
                """,
                [legacy.key, migrated.key],
            ),
            [
                {
                    "source_key": "dcinside-agent-stack",
                    "backfill_anchor_post_id": "523",
                    "state_metadata": '{"history_page_hint":168}',
                },
                {
                    "source_key": "dcinside-ai-utilize",
                    "backfill_anchor_post_id": None,
                    "state_metadata": "{}",
                },
            ],
        )
        self.assertEqual(
            client.query(
                """
                SELECT source_key, oldest_post_id, newest_post_id
                FROM coverage_intervals
                """
            ),
            [
                {
                    "source_key": "dcinside-agent-stack",
                    "oldest_post_id": 523,
                    "newest_post_id": 625,
                }
            ],
        )
        self.assertEqual(
            client.query(
                """
                SELECT source_key, run_type, status
                FROM crawl_runs
                """
            ),
            [
                {
                    "source_key": "dcinside-agent-stack",
                    "run_type": "backfill_history",
                    "status": "completed",
                }
            ],
        )

    def test_empty_post_list_issues_no_query(self) -> None:
        client = RecordingClient()

        upsert_posts(
            client,
            get_target("dcinside-singularity"),
            [],
            "2026-07-16T00:00:00+00:00",
        )

        self.assertEqual(client.calls, [])
        self.assertEqual(post_upsert_query_count(0), 0)

    def test_existing_id_lookup_is_chunked_at_the_d1_parameter_limit(self) -> None:
        client = RecordingClient()
        posts = [
            {**sample_post(post_id), "upvotes": 0, "qualifies_by": "none"}
            for post_id in range(1, EXISTING_POST_IDS_PER_QUERY + 2)
        ]

        update_finalized_posts(
            client,
            get_target("dcinside-singularity"),
            posts,
            "2026-07-16T00:00:00+00:00",
        )

        select_calls = [
            (sql, params)
            for sql, params in client.calls
            if "SELECT canonical_post_key" in sql
        ]
        self.assertEqual(len(select_calls), 2)
        self.assertEqual([len(params) for _, params in select_calls], [100, 2])
        self.assertEqual(existing_post_lookup_query_count(len(posts)), 2)

    def test_three_shared_sources_deduplicate_by_canonical_post_key(self) -> None:
        client = SqliteClient()
        munich_search = get_target("fmkorea-best-munich-search")
        bayern_search = get_target("fmkorea-best-bayern-search")
        board_target = get_target("fmkorea-bayern-board")
        checked_at = "2026-07-22T00:00:00+00:00"
        for target in (munich_search, bayern_search, board_target):
            upsert_source(client, target, checked_at)

        upsert_posts(
            client,
            munich_search,
            [
                {
                    **sample_post(1234),
                    "post_url": "https://www.fmkorea.com/1234?from=search",
                    "subject": "포텐",
                    "title": "first title",
                    "upvotes": 100,
                    "comments": 20,
                }
            ],
            checked_at,
        )
        upsert_posts(
            client,
            bayern_search,
            [
                {
                    **sample_post(1234),
                    "post_url": "https://www.fmkorea.com/1234?from=bayern-search",
                    "subject": "포텐 바이에른 검색",
                    "title": "second title",
                    "upvotes": 120,
                    "comments": 25,
                }
            ],
            "2026-07-22T00:30:00+00:00",
        )
        upsert_posts(
            client,
            board_target,
            [
                {
                    **sample_post(1234),
                    "post_url": "https://www.fmkorea.com/1234",
                    "subject": "바이에른",
                    "title": "latest title",
                    "upvotes": 150,
                    "comments": 30,
                }
            ],
            "2026-07-22T01:00:00+00:00",
        )

        rows = client.query(
            """
            SELECT source_key, archive_key, canonical_post_key, subject,
                   title, post_url, upvotes, comments
            FROM posts
            """
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_key"], munich_search.key)
        self.assertEqual(rows[0]["archive_key"], "fmkorea-munich")
        self.assertEqual(rows[0]["canonical_post_key"], "fmkorea:1234")
        self.assertEqual(rows[0]["subject"], "포텐")
        self.assertEqual(rows[0]["title"], "latest title")
        self.assertEqual(rows[0]["post_url"], "https://www.fmkorea.com/1234")
        self.assertEqual(rows[0]["upvotes"], 150)
        self.assertEqual(rows[0]["comments"], 30)

    def test_new_upsert_repairs_a_legacy_null_canonical_key(self) -> None:
        client = SqliteClient()
        target = get_target("dcinside-singularity")
        checked_at = "2026-07-22T00:00:00+00:00"
        upsert_source(client, target, checked_at)
        client.query(
            """
            INSERT INTO posts (
              source_key, archive_key, external_post_id, post_url, subject,
              title, created_at, created_at_raw, upvotes, comments, fetched_at,
              first_seen_at, last_seen_at, qualifies_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                target.key,
                target.archive_key,
                "9876",
                "https://example.com/legacy",
                "기존",
                "legacy title",
                checked_at,
                checked_at,
                4,
                0,
                checked_at,
                checked_at,
                checked_at,
                "upvotes",
            ],
        )

        upsert_posts(
            client,
            target,
            [{**sample_post(9876), "title": "repaired title"}],
            "2026-07-22T01:00:00+00:00",
        )

        rows = client.query(
            "SELECT canonical_post_key, title, subject FROM posts"
        )
        self.assertEqual(
            rows,
            [
                {
                    "canonical_post_key": "dcinside:thesingularity:9876",
                    "title": "repaired title",
                    "subject": "기존",
                }
            ],
        )

    def test_finalizer_finds_existing_post_from_another_shared_source(self) -> None:
        client = SqliteClient()
        search_target = get_target("fmkorea-best-munich-search")
        board_target = get_target("fmkorea-bayern-board")
        checked_at = "2026-07-22T00:00:00+00:00"
        upsert_source(client, search_target, checked_at)
        upsert_source(client, board_target, checked_at)
        upsert_posts(client, search_target, [sample_post(4321)], checked_at)

        update_finalized_posts(
            client,
            board_target,
            [
                {
                    **sample_post(4321),
                    "upvotes": 0,
                    "comments": 0,
                    "qualifies_by": "none",
                }
            ],
            "2026-07-22T01:00:00+00:00",
        )

        self.assertEqual(
            client.query("SELECT upvotes, comments, qualifies_by FROM posts"),
            [{"upvotes": 0, "comments": 0, "qualifies_by": "none"}],
        )


if __name__ == "__main__":
    unittest.main()
