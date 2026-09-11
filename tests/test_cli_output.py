from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from crawler.cli_output import write_result_file
from crawler.schema_reads import retry_schema_read


class CliOutputTests(unittest.TestCase):
    def test_cp949_environment_emits_lossless_utf8_json(self):
        code = "from crawler.cli_output import configure_utf8_stdio; import json; configure_utf8_stdio(); print(json.dumps({'text': '\u2013 \U0001f600 \ud55c\uae00'}, ensure_ascii=False))"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                env={**os.environ, "PYTHONIOENCODING": "cp949"}, check=True)
        self.assertEqual(json.loads(result.stdout.decode("utf-8")), {"text": "– 😀 한글"})

    def test_result_file_preserves_content_and_replaces_previous_report(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "pipeline-result.json"
            write_result_file(path, {"status": "failed"})
            expected = {"status": "completed", "text": "– 😀 한글", "d1_usage": {"rows_written": 4}}
            write_result_file(path, expected)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), expected)
            self.assertEqual(list(Path(folder).iterdir()), [path])


class SchemaRetryTests(unittest.TestCase):
    @patch("crawler.schema_reads.time.sleep")
    def test_reset_and_timeout_retry_only_until_success(self, sleep):
        inspect = Mock(side_effect=[URLError(ConnectionResetError()), TimeoutError(), {"valid": True}])
        self.assertEqual(retry_schema_read(inspect), {"valid": True})
        self.assertEqual(inspect.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    @patch("crawler.schema_reads.time.sleep")
    def test_persistent_connection_failure_is_still_raised(self, sleep):
        inspect = Mock(side_effect=URLError(ConnectionResetError()))
        with self.assertRaises(URLError):
            retry_schema_read(inspect)
        self.assertEqual(inspect.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    @patch("crawler.schema_reads.time.sleep")
    def test_http_auth_quota_and_schema_errors_are_not_retried(self, sleep):
        for exc in (HTTPError("https://example.test", 403, "Forbidden", {}, io.BytesIO()),
                    URLError("certificate verification failed"), ValueError("invalid schema")):
            with self.subTest(error=type(exc).__name__):
                inspect = Mock(side_effect=exc)
                with self.assertRaises(type(exc)):
                    retry_schema_read(inspect)
                self.assertEqual(inspect.call_count, 1)
        sleep.assert_not_called()


class SchemaCliFailureTests(unittest.TestCase):
    @patch("crawler.schema_reads.time.sleep")
    def test_exhausted_transport_retry_emits_usage_and_failure(self, sleep):
        from crawler.d1 import D1Client
        from crawler.jobs import check_schema
        client = D1Client("test", "test", "test")
        output = io.StringIO()
        with patch.object(client, "_request", side_effect=URLError(ConnectionResetError())), \
             patch.object(check_schema, "D1Client", return_value=client), \
             patch.object(check_schema, "get_required_env", return_value="test"), \
             patch.object(sys, "argv", ["check_schema"]), \
             patch.object(sys, "stdout", output), patch.object(sys, "stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                check_schema.main()
        self.assertEqual(stopped.exception.code, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["d1_usage"]["request_count"], 3)
        self.assertEqual(result["d1_usage"]["failed_request_count"], 3)
