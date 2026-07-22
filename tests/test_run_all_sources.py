from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from crawler.jobs.run_all_sources import (
    CYCLE_MODE_BACKFILL,
    CYCLE_MODE_HOT,
    dc_cycle_config,
    iter_github_scheduled_targets,
    run_all_targets,
)
from crawler.targets import get_target, iter_targets


class RunAllSourcesTests(unittest.TestCase):
    def test_github_scheduled_sweeps_run_only_dc_targets(self) -> None:
        scheduled_keys = [target.key for target in iter_github_scheduled_targets()]
        self.assertEqual(
            [target.key for target in iter_targets()],
            [
                "dcinside-singularity",
                "dcinside-agent-stack",
                "fmkorea-best-munich-search",
                "fmkorea-bayern-board",
            ],
        )
        self.assertEqual(
            scheduled_keys,
            ["dcinside-singularity", "dcinside-agent-stack"],
        )

        for mode in (CYCLE_MODE_HOT, CYCLE_MODE_BACKFILL):
            with self.subTest(mode=mode):
                calls = []

                def runner(target, selected_mode, client):
                    calls.append((target.key, selected_mode, client))
                    return {"target": target.key, "status": "completed"}

                result = run_all_targets(mode=mode, runner=runner)

                self.assertEqual([key for key, _, _ in calls], scheduled_keys)
                self.assertEqual(
                    [selected_mode for _, selected_mode, _ in calls],
                    [mode, mode],
                )
                self.assertEqual(result["target_count"], 2)
                self.assertEqual(result["failure_count"], 0)
                self.assertEqual(result["status"], "completed")

    def test_one_failure_does_not_stop_an_independent_origin(self) -> None:
        calls = []

        def runner(target, mode, client):
            calls.append(target.key)
            if target.key == "dcinside-singularity":
                return {"target": target.key, "status": "blocked"}
            return {"target": target.key, "status": "completed"}

        result = run_all_targets(
            mode=CYCLE_MODE_BACKFILL,
            targets=iter_targets(),
            runner=runner,
        )

        self.assertEqual(
            calls,
            [
                "dcinside-singularity",
                "fmkorea-best-munich-search",
                "fmkorea-bayern-board",
            ],
        )
        self.assertEqual(result["results"][1]["stop_reason"], "origin_blocked")
        self.assertEqual(result["status"], "failed")

    def test_existing_cooldown_skips_same_origin_without_failing_sweep(self) -> None:
        calls = []

        def runner(target, mode, client):
            calls.append(target.key)
            if target.key in {
                "dcinside-singularity",
                "fmkorea-best-munich-search",
            }:
                return {"target": target.key, "status": "cooldown"}
            raise AssertionError("a same-origin feed should not be requested")

        result = run_all_targets(
            mode=CYCLE_MODE_HOT,
            targets=iter_targets(),
            runner=runner,
        )

        self.assertEqual(
            calls,
            ["dcinside-singularity", "fmkorea-best-munich-search"],
        )
        self.assertEqual(
            [item["status"] for item in result["results"]],
            ["cooldown", "cooldown", "cooldown", "cooldown"],
        )
        self.assertEqual(result["status"], "completed")

    def test_singularity_manual_override_does_not_change_agent_policy(self) -> None:
        override = {
            "TC_HOT_LOOKBACK_MINUTES": "600",
            "TC_HOT_MAX_SECONDS": "300",
            "TC_CYCLE_MAX_SECONDS": "300",
        }
        with patch.dict(os.environ, override, clear=False):
            singularity = dc_cycle_config(
                get_target("dcinside-singularity"), CYCLE_MODE_HOT
            )
            agent = dc_cycle_config(
                get_target("dcinside-agent-stack"), CYCLE_MODE_HOT
            )

        self.assertEqual(singularity.hot_lookback_minutes, 600)
        self.assertEqual(singularity.hot_max_seconds, 300)
        self.assertEqual(agent.hot_lookback_minutes, 240)
        self.assertEqual(agent.hot_max_seconds, 240)

    def test_backfill_config_reserves_positive_history_window(self) -> None:
        for target_key in ("dcinside-singularity", "dcinside-agent-stack"):
            with self.subTest(target=target_key):
                with patch.dict(os.environ, {}, clear=True):
                    config = dc_cycle_config(
                        get_target(target_key), CYCLE_MODE_BACKFILL
                    )
                self.assertGreater(config.deep_reserved_seconds, 0)
                self.assertLess(
                    config.hot_max_seconds + config.deep_reserved_seconds,
                    config.cycle_max_seconds,
                )

    def test_unknown_mode_is_rejected_before_any_target_runs(self) -> None:
        with self.assertRaises(ValueError):
            run_all_targets(mode="full", runner=lambda *_: {})


if __name__ == "__main__":
    unittest.main()
