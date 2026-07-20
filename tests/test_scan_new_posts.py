from __future__ import annotations

import http.client
import socket
import unittest
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
)
from crawler.targets import get_target


class RecordingClient:
    def __init__(self) -> None:
        self.calls = []

    def query(self, sql, params=None):
        self.calls.append((sql, list(params or [])))
        return []


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
        for status in (403, 429):
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
        self.assertEqual([len(params) for _, params in client.calls], [91, 13])
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
            "ON CONFLICT(source_key, external_post_id) DO UPDATE SET",
            1,
        )
        self.assertIn("subject", insert_clause)
        self.assertNotIn("subject", update_clause)
        self.assertIn("일반", params)

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
            if "SELECT external_post_id" in sql
        ]
        self.assertEqual(len(select_calls), 2)
        self.assertEqual([len(params) for _, params in select_calls], [100, 2])
        self.assertEqual(existing_post_lookup_query_count(len(posts)), 2)


if __name__ == "__main__":
    unittest.main()
