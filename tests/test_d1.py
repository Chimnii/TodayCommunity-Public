from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from crawler.d1 import D1Client


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


if __name__ == "__main__":
    unittest.main()
