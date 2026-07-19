from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_WORKFLOW_TEMPLATES = ROOT / "public-mirror" / ".github" / "workflows"
IS_PRIVATE_SOURCE = PUBLIC_WORKFLOW_TEMPLATES.is_dir()
WORKFLOWS = (
    PUBLIC_WORKFLOW_TEMPLATES
    if IS_PRIVATE_SOURCE
    else ROOT / ".github" / "workflows"
)
ACTIVE_PRIVATE_WORKFLOWS = ROOT / ".github" / "workflows"
CHECKOUT_PIN = (
    "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0"
)
SETUP_PYTHON_PIN = (
    "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0"
)


class CrawlWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hot = (WORKFLOWS / "scan-dcinside.yml").read_text(encoding="utf-8")
        self.backfill = (
            WORKFLOWS / "scan-dcinside-backfill.yml"
        ).read_text(encoding="utf-8")
        self.deploy_pages = (WORKFLOWS / "deploy-pages.yml").read_text(
            encoding="utf-8"
        )
        self.deploy_scheduler = (WORKFLOWS / "deploy-scheduler.yml").read_text(
            encoding="utf-8"
        )

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
        for workflow in (
            self.hot,
            self.backfill,
            self.deploy_pages,
            self.deploy_scheduler,
        ):
            self.assertRegex(
                workflow,
                r"(?m)^permissions:\s*\n\s+contents: read\s*$",
            )
            self.assertNotIn("pull_request_target:", workflow)
            self.assertNotRegex(workflow, r"(?m)^\s*[A-Za-z_-]+:\s*write\s*$")

        for workflow in (self.hot, self.backfill):
            self.assertRegex(workflow, r"(?m)^\s+environment: collection\s*$")
        for workflow in (self.deploy_pages, self.deploy_scheduler):
            self.assertRegex(workflow, r"(?m)^\s+environment: production\s*$")

    def test_deployment_workflows_use_locked_wrangler_and_split_tokens(self) -> None:
        for workflow in (self.deploy_pages, self.deploy_scheduler):
            self.assertIn(CHECKOUT_PIN, workflow)
            self.assertIn("npm ci --ignore-scripts", workflow)
            self.assertIn("./node_modules/.bin/wrangler", workflow)
            self.assertNotIn("npx ", workflow)

        self.assertIn("secrets.CLOUDFLARE_PAGES_API_TOKEN", self.deploy_pages)
        self.assertNotIn("secrets.CLOUDFLARE_WORKERS_API_TOKEN", self.deploy_pages)
        self.assertIn(
            "secrets.CLOUDFLARE_SCHEDULER_API_TOKEN", self.deploy_scheduler
        )
        self.assertNotIn("secrets.CLOUDFLARE_PAGES_API_TOKEN", self.deploy_scheduler)
        self.assertNotIn("secrets.CLOUDFLARE_WORKERS_API_TOKEN", self.deploy_scheduler)

        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(package["devDependencies"]["wrangler"], "4.112.0")
        self.assertEqual(lock["packages"][""]["devDependencies"]["wrangler"], "4.112.0")

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

    def test_private_source_has_no_active_crawl_workflows_after_cutover(self) -> None:
        if not IS_PRIVATE_SOURCE:
            self.skipTest("running in the public mirror")

        private_hot_path = ACTIVE_PRIVATE_WORKFLOWS / "scan-dcinside.yml"
        private_backfill_path = (
            ACTIVE_PRIVATE_WORKFLOWS / "scan-dcinside-backfill.yml"
        )
        self.assertFalse(private_hot_path.exists())
        self.assertFalse(private_backfill_path.exists())

    def test_local_secret_and_cloudflare_state_patterns_are_ignored(self) -> None:
        ignore_lines = {
            line.strip()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertTrue(
            {
                ".env",
                ".env.*",
                ".dev.vars",
                ".dev.vars.*",
                ".wrangler/",
                "node_modules/",
            }
            <= ignore_lines
        )
        self.assertIn("!.env.example", ignore_lines)


if __name__ == "__main__":
    unittest.main()
