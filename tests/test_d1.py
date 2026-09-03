from __future__ import annotations

import json
import sqlite3
import unittest
from io import BytesIO
from urllib import error
from unittest.mock import patch

from crawler.d1 import (
    D1Client,
    d1_usage_label,
    d1_usage_snapshot,
    d1_usage_summary,
    split_sql_statements,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class D1ClientResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = D1Client("account", "database", "token")

    def test_query_returns_rows_from_a_successful_result(self) -> None:
        payload = {
            "success": True,
            "errors": [],
            "result": [
                {
                    "success": True,
                    "results": [{"value": 1}],
                    "meta": {},
                }
            ],
        }

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ):
            rows = self.client.query("SELECT 1 AS value")

        self.assertEqual(rows, [{"value": 1}])

    def test_query_collects_rows_by_label_without_changing_return_value(self) -> None:
        payload = {
            "success": True,
            "errors": [],
            "result": [
                {
                    "success": True,
                    "results": [{"value": 1}],
                    "meta": {"rows_read": 7, "rows_written": 2},
                }
            ],
        }

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ):
            rows = self.client.query("SELECT 1 AS value", label="coverage")

        self.assertEqual(rows, [{"value": 1}])
        self.assertEqual(
            self.client.usage_summary(),
            {
                "request_count": 1,
                "failed_request_count": 0,
                "statement_count": 1,
                "rows_read": 7,
                "rows_written": 2,
                "incomplete_meta_count": 0,
                "labels": {
                    "coverage": {
                        "statement_count": 1,
                        "rows_read": 7,
                        "rows_written": 2,
                        "incomplete_meta_count": 0,
                        "failed_request_count": 0,
                    }
                },
            },
        )

    def test_default_timeout_is_forwarded_to_urlopen(self) -> None:
        payload = {
            "success": True,
            "errors": [],
            "result": [
                {
                    "success": True,
                    "results": [{"value": 1}],
                    "meta": {},
                }
            ],
        }

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ) as urlopen:
            self.client.query("SELECT 1 AS value")

        self.assertEqual(self.client.timeout_seconds, 30.0)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 30.0)

    def test_http_error_surfaces_the_d1_quota_message_without_request_data(self) -> None:
        payload = {
            "success": False,
            "errors": [
                {
                    "code": 7500,
                    "message": (
                        "Your account has exceeded D1's free tier daily row write "
                        "limit. Upgrade to a paid plan or wait until tomorrow."
                    ),
                }
            ],
        }
        http_error = error.HTTPError(
            "https://api.cloudflare.com/client/v4/d1",
            400,
            "Bad Request",
            {},
            BytesIO(json.dumps(payload).encode("utf-8")),
        )

        with patch(
            "crawler.d1.request.urlopen",
            side_effect=http_error,
        ), self.assertRaisesRegex(
            RuntimeError,
            "free tier daily row write limit",
        ) as raised:
            self.client.execute("INSERT INTO private_table VALUES (?)", ["secret"])

        self.assertNotIn("private_table", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_batch_sends_exact_statement_payload_and_returns_ordered_results(self) -> None:
        payload = {
            "success": True,
            "errors": [],
            "result": [
                {
                    "success": True,
                    "results": [],
                    "meta": {"changes": 1},
                },
                {
                    "success": True,
                    "results": [],
                    "meta": {"changes": 2},
                },
            ],
        }

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ) as urlopen:
            results = self.client.batch(
                [
                    ("INSERT INTO first_table (value) VALUES (?)", ["first"]),
                    (
                        "UPDATE second_table SET value = ? WHERE id = ?",
                        ("second", 2),
                    ),
                ]
            )

        http_request = urlopen.call_args.args[0]
        self.assertEqual(
            json.loads(http_request.data.decode("utf-8")),
            {
                "batch": [
                    {
                        "sql": "INSERT INTO first_table (value) VALUES (?)",
                        "params": ["first"],
                    },
                    {
                        "sql": "UPDATE second_table SET value = ? WHERE id = ?",
                        "params": ["second", 2],
                    },
                ]
            },
        )
        self.assertEqual(results, payload["result"])

    def test_batch_collects_each_statement_under_its_own_label(self) -> None:
        payload = {
            "success": True,
            "errors": [],
            "result": [
                {
                    "success": True,
                    "results": [],
                    "meta": {"rows_read": 3, "rows_written": 1},
                },
                {
                    "success": True,
                    "results": [],
                    "meta": {"rows_read": 5, "rows_written": 2},
                },
                {
                    "success": True,
                    "results": [],
                    "meta": {"rows_read": 0, "rows_written": 1},
                },
            ],
        }

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ):
            results = self.client.batch(
                [
                    ("INSERT INTO posts DEFAULT VALUES", []),
                    ("UPDATE posts SET fetched_at = NULL", []),
                    ("UPDATE archive_stats SET active_post_count = 1", []),
                ],
                labels=("post.upsert", "post.heartbeat", "stats"),
            )

        self.assertEqual(results, payload["result"])
        summary = self.client.usage_summary()
        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["statement_count"], 3)
        self.assertEqual(summary["rows_read"], 8)
        self.assertEqual(summary["rows_written"], 4)
        self.assertEqual(
            summary["labels"]["post.heartbeat"]["rows_read"],
            5,
        )
        self.assertEqual(summary["labels"]["stats"]["rows_written"], 1)

    def test_missing_and_partial_meta_are_counted_without_guessing(self) -> None:
        payload = {
            "success": True,
            "errors": [],
            "result": [
                {"success": True, "results": []},
                {
                    "success": True,
                    "results": [],
                    "meta": {"rows_read": 4},
                },
                {
                    "success": True,
                    "results": [],
                    "meta": {"rows_read": 1, "rows_written": "2"},
                },
            ],
        }

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ):
            self.client.batch(
                [
                    ("SELECT 1", []),
                    ("SELECT 2", []),
                    ("SELECT 3", []),
                ],
                label="stats",
            )

        summary = self.client.usage_summary()
        self.assertEqual(summary["rows_read"], 5)
        self.assertEqual(summary["rows_written"], 0)
        self.assertEqual(summary["incomplete_meta_count"], 3)
        self.assertEqual(summary["labels"]["stats"]["statement_count"], 3)

    def test_failed_response_records_only_safe_aggregate_counters(self) -> None:
        payload = {
            "success": True,
            "errors": [],
            "result": [
                {
                    "success": False,
                    "error": "constraint failed",
                    "results": [],
                }
            ],
        }

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ), self.assertRaisesRegex(RuntimeError, "constraint failed"):
            self.client.query(
                "INSERT INTO private_table VALUES (?)",
                ["secret-value"],
                label="post.upsert",
            )

        summary = self.client.usage_summary()
        self.assertEqual(summary["request_count"], 1)
        self.assertEqual(summary["failed_request_count"], 1)
        self.assertEqual(summary["statement_count"], 0)
        self.assertEqual(
            summary["labels"]["post.upsert"]["failed_request_count"],
            1,
        )
        serialized = json.dumps(summary)
        self.assertNotIn("private_table", serialized)
        self.assertNotIn("secret-value", serialized)
        self.assertNotIn("token", serialized)

    def test_snapshot_exposes_one_callers_total_across_work_labels(self) -> None:
        def response(rows_read: int, rows_written: int) -> FakeResponse:
            return FakeResponse(
                {
                    "success": True,
                    "errors": [],
                    "result": [
                        {
                            "success": True,
                            "results": [],
                            "meta": {
                                "rows_read": rows_read,
                                "rows_written": rows_written,
                            },
                        }
                    ],
                }
            )

        with patch(
            "crawler.d1.request.urlopen",
            side_effect=[
                response(2, 3),
                response(4, 5),
                response(6, 1),
                response(7, 2),
                response(8, 3),
            ],
        ):
            self.client.execute("bootstrap", label="source.bootstrap")
            checkpoint = d1_usage_snapshot(self.client)
            self.assertIsNotNone(checkpoint)
            self.client.execute("upsert", label="post.upsert")
            self.client.execute("stats", label="stats")
            with d1_usage_label(self.client, "game-news.candidate"):
                self.client.execute("game-news")
            with d1_usage_label(self.client, "topic"):
                self.client.execute("topic")

        summary = d1_usage_summary(self.client, checkpoint)
        self.assertIsNotNone(summary)
        self.assertEqual(summary["request_count"], 4)
        self.assertEqual(summary["statement_count"], 4)
        self.assertEqual(summary["rows_read"], 25)
        self.assertEqual(summary["rows_written"], 11)
        self.assertEqual(
            set(summary["labels"]),
            {"post.upsert", "stats", "game-news.candidate", "topic"},
        )

    def test_batch_requires_one_result_for_each_statement(self) -> None:
        payload = {
            "success": True,
            "errors": [],
            "result": [
                {
                    "success": True,
                    "results": [],
                    "meta": {},
                }
            ],
        }

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ), self.assertRaisesRegex(RuntimeError, "expected 2, got 1"):
            self.client.batch(
                [
                    ("INSERT INTO first_table DEFAULT VALUES", []),
                    ("INSERT INTO second_table DEFAULT VALUES", []),
                ]
            )

    def test_empty_batch_is_rejected_without_an_http_request(self) -> None:
        with patch("crawler.d1.request.urlopen") as urlopen:
            with self.assertRaisesRegex(ValueError, "at least one statement"):
                self.client.batch([])

        urlopen.assert_not_called()

    def test_nested_query_failure_is_not_treated_as_success(self) -> None:
        payload = {
            "success": True,
            "errors": [],
            "result": [
                {
                    "success": False,
                    "error": "constraint failed",
                    "results": [],
                }
            ],
        }

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ), self.assertRaisesRegex(RuntimeError, "constraint failed"):
            self.client.query("INSERT INTO posts VALUES (?)", [1])

    def test_nested_result_without_explicit_success_is_rejected(self) -> None:
        payload = {
            "success": True,
            "errors": [],
            "result": [{"results": []}],
        }

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ), self.assertRaisesRegex(RuntimeError, "success=None"):
            self.client.query("SELECT 1")

    def test_malformed_result_shape_is_rejected(self) -> None:
        payload = {
            "success": True,
            "errors": [],
            "result": {"results": []},
        }

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ), self.assertRaisesRegex(RuntimeError, "invalid result shape"):
            self.client.query("SELECT 1")

    def test_empty_statement_result_is_rejected(self) -> None:
        payload = {"success": True, "errors": [], "result": []}

        with patch(
            "crawler.d1.request.urlopen",
            return_value=FakeResponse(payload),
        ), self.assertRaisesRegex(RuntimeError, "no statement result"):
            self.client.query("SELECT 1")


class SqlScriptSplittingTests(unittest.TestCase):
    def test_trigger_body_is_kept_as_one_statement(self) -> None:
        script = """
        CREATE TABLE events (id INTEGER PRIMARY KEY, value TEXT);
        CREATE TRIGGER events_no_update
        BEFORE UPDATE ON events
        BEGIN
          SELECT RAISE(ABORT, 'events are append-only');
        END;
        INSERT INTO events (value) VALUES ('a; b');
        """

        statements = split_sql_statements(script)

        self.assertEqual(len(statements), 3)
        self.assertIn("SELECT RAISE", statements[1])
        self.assertTrue(statements[1].rstrip().endswith("END"))
        self.assertIn("'a; b'", statements[2])

    def test_current_fresh_schema_splits_into_executable_statements(self) -> None:
        from pathlib import Path

        schema = (
            Path(__file__).resolve().parents[1] / "cloudflare" / "schema.sql"
        ).read_text(encoding="utf-8")
        connection = sqlite3.connect(":memory:")

        for statement in split_sql_statements(schema):
            connection.execute(statement)

        trigger_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'trigger' "
                "AND name LIKE 'game_news_%'"
            )
        }
        self.assertEqual(
            trigger_names,
            {
                "game_news_evaluations_no_update",
                "game_news_evaluations_no_delete",
                "game_news_feedback_no_update",
                "game_news_feedback_no_delete",
                "game_news_visibility_events_no_update",
                "game_news_visibility_events_no_delete",
                "game_news_manual_rule_events_no_update",
                "game_news_manual_rule_events_no_delete",
            },
        )


if __name__ == "__main__":
    unittest.main()
