from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_WORKFLOW_TEMPLATES = ROOT / "public-mirror" / ".github" / "workflows"
WORKFLOWS = (
    PUBLIC_WORKFLOW_TEMPLATES
    if PUBLIC_WORKFLOW_TEMPLATES.is_dir()
    else ROOT / ".github" / "workflows"
)
ACTIVE_PRIVATE_WORKFLOWS = ROOT / ".github" / "workflows"
CHECKOUT_PIN = (
    "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4.3.1"
)
SETUP_PYTHON_PIN = (
    "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0"
)


class CrawlWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hot = (WORKFLOWS / "scan-dcinside.yml").read_text(encoding="utf-8")
        self.backfill = (
            WORKFLOWS / "scan-dcinside-backfill.yml"
        ).read_text(encoding="utf-8")

    def test_workflows_share_one_non_cancelling_concurrency_group(self) -> None:
        for workflow in (self.hot, self.backfill):
            self.assertRegex(workflow, r"(?m)^\s*group: scan-dcinside\s*$")
            self.assertRegex(workflow, r"(?m)^\s*cancel-in-progress: false\s*$")

    def test_workflows_pin_actions_and_do_not_persist_checkout_credentials(self) -> None:
        for workflow in (self.hot, self.backfill):
            self.assertIn(CHECKOUT_PIN, workflow)
            self.assertIn(SETUP_PYTHON_PIN, workflow)
            self.assertRegex(
                workflow,
                r"(?m)^\s*persist-credentials: false\s*$",
            )
            for action in re.findall(r"(?m)^\s*uses:\s*([^\s]+)", workflow):
                self.assertRegex(
                    action,
                    r"^[^@]+@[0-9a-f]{40}$",
                    msg=f"workflow action is not pinned to a full commit: {action}",
                )

    def test_public_workflows_keep_minimal_permissions_and_safe_triggers(self) -> None:
        for workflow in (self.hot, self.backfill):
            self.assertRegex(
                workflow,
                r"(?m)^permissions:\s*\n\s+contents: read\s*$",
            )
            self.assertNotIn("pull_request_target:", workflow)
            self.assertNotRegex(workflow, r"(?m)^\s*[A-Za-z_-]+:\s*write\s*$")

    def test_hot_dispatch_and_budget_contract(self) -> None:
        self.assertIn("workflow_dispatch:", self.hot)
        self.assertNotRegex(self.hot, r"(?m)^\s*schedule:\s*$")
        self.assertIn('TC_HOT_LOOKBACK_MINUTES: "180"', self.hot)
        self.assertIn('TC_HOT_MAX_SECONDS: "180"', self.hot)
        self.assertIn('TC_CYCLE_MAX_SECONDS: "180"', self.hot)
        self.assertIn('TC_DEEP_RESERVED_SECONDS: "0"', self.hot)
        self.assertIn("--mode hot", self.hot)
        self.assertNotIn("check_schema", self.hot)

    def test_backfill_dispatch_and_budget_contract(self) -> None:
        self.assertIn("workflow_dispatch:", self.backfill)
        self.assertNotRegex(self.backfill, r"(?m)^\s*schedule:\s*$")
        self.assertIn('TC_CYCLE_MAX_SECONDS: "600"', self.backfill)
        self.assertIn('TC_DEEP_RESERVED_SECONDS: "300"', self.backfill)
        self.assertIn("--mode backfill", self.backfill)
        self.assertIn("check_schema", self.backfill)

    def test_no_scheduled_production_workflow_runs_the_combined_mode(self) -> None:
        for path in WORKFLOWS.glob("*.yml"):
            workflow = path.read_text(encoding="utf-8")
            if re.search(r"(?m)^\s*schedule:\s*$", workflow):
                self.assertNotRegex(
                    workflow,
                    r"python -m crawler\.jobs\.run_cycle(?![^\n]*--mode)",
                    msg=f"{path.name} still schedules the combined crawl mode",
                )

    def test_transition_private_schedules_keep_the_expected_cadence(self) -> None:
        if WORKFLOWS == ACTIVE_PRIVATE_WORKFLOWS:
            self.skipTest("running in the public mirror")

        private_hot_path = ACTIVE_PRIVATE_WORKFLOWS / "scan-dcinside.yml"
        private_backfill_path = (
            ACTIVE_PRIVATE_WORKFLOWS / "scan-dcinside-backfill.yml"
        )
        if not private_hot_path.exists() and not private_backfill_path.exists():
            self.skipTest("private scheduled workflows were removed after cutover")

        self.assertTrue(private_hot_path.is_file())
        self.assertTrue(private_backfill_path.is_file())
        private_hot = private_hot_path.read_text(encoding="utf-8")
        private_backfill = private_backfill_path.read_text(encoding="utf-8")
        self.assertIn('cron: "7,22,37,52 * * * *"', private_hot)
        self.assertIn('cron: "56 */6 * * *"', private_backfill)

    def test_local_secret_and_cloudflare_state_patterns_are_ignored(self) -> None:
        ignore_lines = {
            line.strip()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertTrue(
            {".env", ".env.*", ".dev.vars", ".dev.vars.*", ".wrangler/"}
            <= ignore_lines
        )
        self.assertIn("!.env.example", ignore_lines)


if __name__ == "__main__":
    unittest.main()
