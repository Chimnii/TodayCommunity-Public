from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from crawler.fmkorea_browser import FmkoreaBrowserConfig
from crawler.jobs.run_fmkorea_sources import (
    FMKOREA_LOCAL_TARGET_KEYS,
    iter_local_fmkorea_targets,
    parse_args,
    run_local_fmkorea_sources,
)


class FakeChromeSession:
    instances: list["FakeChromeSession"] = []

    def __init__(self, config) -> None:
        self.config = config
        self.entered = False
        self.exited = False
        self.cleanup_warnings = []
        self.__class__.instances.append(self)

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exited = True


class LocalFmkoreaSweepTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeChromeSession.instances.clear()
        self.config = FmkoreaBrowserConfig(
            profile_dir=Path("synthetic-profile"),
            chrome_executable_path=Path("synthetic-chrome.exe"),
        )

    def test_allowlist_contains_only_the_three_fmkorea_hot_feeds(self) -> None:
        targets = iter_local_fmkorea_targets()
        self.assertEqual(tuple(target.key for target in targets), FMKOREA_LOCAL_TARGET_KEYS)
        self.assertEqual(
            FMKOREA_LOCAL_TARGET_KEYS,
            (
                "fmkorea-best-munich-search",
                "fmkorea-best-bayern-search",
                "fmkorea-bayern-board",
            ),
        )
        self.assertEqual({target.origin_key for target in targets}, {"fmkorea"})

    def test_three_feeds_share_one_serial_browser_session(self) -> None:
        observed = []
        observed_page_limits = []

        def run_target(*, target, mode, client, fetcher):
            observed.append((target.key, mode, client, fetcher))
            observed_page_limits.append(target.hot_max_pages)
            return {
                "target": target.key,
                "archive": target.archive_key,
                "status": "completed",
            }

        with (
            patch(
                "crawler.jobs.run_fmkorea_sources.FmkoreaChromeSession",
                FakeChromeSession,
            ),
            patch(
                "crawler.jobs.run_fmkorea_sources.run_fmkorea_target",
                side_effect=run_target,
            ),
        ):
            result = run_local_fmkorea_sources(
                mode="hot",
                browser_config=self.config,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual([item[0] for item in observed], list(FMKOREA_LOCAL_TARGET_KEYS))
        self.assertEqual(observed_page_limits, [30, 30, 30])
        self.assertEqual(len({id(item[3]) for item in observed}), 1)
        self.assertTrue(FakeChromeSession.instances[0].entered)
        self.assertTrue(FakeChromeSession.instances[0].exited)

    def test_one_page_smoke_caps_all_targets_without_changing_hot_sweep(self) -> None:
        observed = []
        low_interval_config = FmkoreaBrowserConfig(
            profile_dir=Path("synthetic-profile"),
            chrome_executable_path=Path("synthetic-chrome.exe"),
            min_navigation_interval_seconds=1.0,
        )

        def run_target(*, target, mode, client, fetcher):
            observed.append((target, mode, fetcher))
            return {
                "target": target.key,
                "archive": target.archive_key,
                "status": "completed",
            }

        with (
            patch(
                "crawler.jobs.run_fmkorea_sources.FmkoreaChromeSession",
                FakeChromeSession,
            ),
            patch(
                "crawler.jobs.run_fmkorea_sources.run_fmkorea_target",
                side_effect=run_target,
            ),
        ):
            result = run_local_fmkorea_sources(
                mode="hot",
                browser_config=low_interval_config,
                max_pages_per_target=1,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(
            [item[0].key for item in observed],
            list(FMKOREA_LOCAL_TARGET_KEYS),
        )
        self.assertEqual([item[0].hot_max_pages for item in observed], [1, 1, 1])
        self.assertEqual([item[1] for item in observed], ["hot", "hot", "hot"])
        self.assertEqual(len({id(item[2]) for item in observed}), 1)
        self.assertEqual(
            FakeChromeSession.instances[0].config.min_navigation_interval_seconds,
            10.0,
        )
        self.assertEqual(
            [target.hot_max_pages for target in iter_local_fmkorea_targets()],
            [30, 30, 30],
        )

    def test_smoke_cap_never_raises_the_configured_page_limit(self) -> None:
        observed_pages = []

        with (
            patch(
                "crawler.jobs.run_fmkorea_sources.FmkoreaChromeSession",
                FakeChromeSession,
            ),
            patch(
                "crawler.jobs.run_fmkorea_sources.run_fmkorea_target",
                side_effect=lambda *, target, **kwargs: (
                    observed_pages.append(target.hot_max_pages)
                    or {
                        "target": target.key,
                        "archive": target.archive_key,
                        "status": "completed",
                    }
                ),
            ),
        ):
            run_local_fmkorea_sources(
                mode="hot",
                browser_config=self.config,
                max_pages_per_target=999,
            )

        self.assertEqual(observed_pages, [30, 30, 30])

    def test_browser_gate_never_undercuts_source_request_intervals(self) -> None:
        low_interval_config = FmkoreaBrowserConfig(
            profile_dir=Path("synthetic-profile"),
            chrome_executable_path=Path("synthetic-chrome.exe"),
            min_navigation_interval_seconds=1.0,
        )
        with (
            patch(
                "crawler.jobs.run_fmkorea_sources.FmkoreaChromeSession",
                FakeChromeSession,
            ),
            patch(
                "crawler.jobs.run_fmkorea_sources.run_fmkorea_target",
                side_effect=lambda *, target, **kwargs: {
                    "target": target.key,
                    "archive": target.archive_key,
                    "status": "completed",
                },
            ),
        ):
            run_local_fmkorea_sources(
                mode="hot",
                browser_config=low_interval_config,
            )

        self.assertEqual(
            FakeChromeSession.instances[0].config.min_navigation_interval_seconds,
            10.0,
        )

    def test_direct_sweep_rejects_backfill_before_opening_browser(self) -> None:
        with patch(
            "crawler.jobs.run_fmkorea_sources.FmkoreaChromeSession",
            FakeChromeSession,
        ):
            with self.assertRaisesRegex(ValueError, "expected 'hot'"):
                run_local_fmkorea_sources(
                    mode="backfill",
                    browser_config=self.config,
                )

        self.assertEqual(FakeChromeSession.instances, [])

    def test_direct_sweep_rejects_invalid_smoke_caps_before_opening_browser(self) -> None:
        invalid_values = (0, -1, True, False, 1.5, "1")
        with patch(
            "crawler.jobs.run_fmkorea_sources.FmkoreaChromeSession",
            FakeChromeSession,
        ):
            for value in invalid_values:
                with self.subTest(value=value):
                    with self.assertRaisesRegex(ValueError, "positive integer"):
                        run_local_fmkorea_sources(
                            mode="hot",
                            browser_config=self.config,
                            max_pages_per_target=value,
                        )

        self.assertEqual(FakeChromeSession.instances, [])

    def test_cli_accepts_positive_smoke_cap(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_fmkorea_sources",
                "--mode",
                "hot",
                "--max-pages-per-target",
                "1",
            ],
        ):
            args = parse_args()

        self.assertEqual(args.max_pages_per_target, 1)

    def test_cli_rejects_non_positive_or_non_integer_smoke_caps(self) -> None:
        for value in ("0", "-1", "1.5"):
            with self.subTest(value=value):
                with (
                    patch.object(
                        sys,
                        "argv",
                        [
                            "run_fmkorea_sources",
                            "--mode",
                            "hot",
                            "--max-pages-per-target",
                            value,
                        ],
                    ),
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    parse_args()

                self.assertEqual(raised.exception.code, 2)

    def test_cli_rejects_backfill_mode(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                ["run_fmkorea_sources", "--mode", "backfill"],
            ),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as raised,
        ):
            parse_args()

        self.assertEqual(raised.exception.code, 2)

    def test_first_origin_block_skips_remaining_feeds_without_calling_runner(self) -> None:
        observed = []

        def blocked_first(*, target, mode, client, fetcher):
            observed.append(target.key)
            return {
                "target": target.key,
                "archive": target.archive_key,
                "status": "blocked",
                "blocked_reason": "synthetic HTTP 430",
            }

        with (
            patch(
                "crawler.jobs.run_fmkorea_sources.FmkoreaChromeSession",
                FakeChromeSession,
            ),
            patch(
                "crawler.jobs.run_fmkorea_sources.run_fmkorea_target",
                side_effect=blocked_first,
            ),
        ):
            result = run_local_fmkorea_sources(
                mode="hot",
                browser_config=self.config,
            )

        self.assertEqual(observed, [FMKOREA_LOCAL_TARGET_KEYS[0]])
        self.assertEqual(result["results"][0]["status"], "blocked")
        self.assertEqual(
            [item["status"] for item in result["results"][1:]],
            ["blocked", "blocked"],
        )
        self.assertEqual(
            [item["stop_reason"] for item in result["results"][1:]],
            ["origin_blocked", "origin_blocked"],
        )

    def test_browser_cleanup_warning_fails_the_sweep_observably(self) -> None:
        class WarningChromeSession(FakeChromeSession):
            def __exit__(self, exc_type, exc, traceback):
                super().__exit__(exc_type, exc, traceback)
                self.cleanup_warnings.append("synthetic cleanup failure")

        with (
            patch(
                "crawler.jobs.run_fmkorea_sources.FmkoreaChromeSession",
                WarningChromeSession,
            ),
            patch(
                "crawler.jobs.run_fmkorea_sources.run_fmkorea_target",
                side_effect=lambda *, target, **kwargs: {
                    "target": target.key,
                    "archive": target.archive_key,
                    "status": "completed",
                },
            ),
        ):
            result = run_local_fmkorea_sources(
                mode="hot",
                browser_config=self.config,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_count"], 1)
        self.assertEqual(
            result["browser_cleanup_warnings"],
            ["synthetic cleanup failure"],
        )


if __name__ == "__main__":
    unittest.main()
