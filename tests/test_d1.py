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
