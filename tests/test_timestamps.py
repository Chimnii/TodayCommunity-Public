from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from crawler.timestamps import canonical_utc, canonicalize_utc_text


class CanonicalTimestampTests(unittest.TestCase):
    def test_converts_offset_datetime_to_fixed_width_utc(self) -> None:
        self.assertEqual(
            canonical_utc(
                datetime(
                    2026,
                    8,
                    29,
                    9,
                    30,
                    45,
                    987654,
                    tzinfo=timezone(timedelta(hours=9)),
                )
            ),
            "2026-08-29T00:30:45Z",
        )

    def test_canonicalizes_supported_iso_8601_text_forms(self) -> None:
        cases = {
            "2026-08-29T00:30:45Z": "2026-08-29T00:30:45Z",
            "2026-08-29T00:30:45+00:00": "2026-08-29T00:30:45Z",
            "2026-08-29T09:30:45+09:00": "2026-08-29T00:30:45Z",
            "2026-08-29 00:30:45+00:00": "2026-08-29T00:30:45Z",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(canonicalize_utc_text(raw), expected)

    def test_rejects_naive_or_invalid_timestamps(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            canonical_utc(datetime(2026, 8, 29, 0, 30, 45))
        for value in ("", "2026-08-29T00:30:45", "not-a-time", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    canonicalize_utc_text(value)


if __name__ == "__main__":
    unittest.main()
