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
GITHUB_SCRIPT_PIN = (
    "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3 # v9.0.0"
)
SETUP_NODE_PIN = (
    "actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0"
)
FAILURE_STREAK_SUCCESS_MARKER = "Failure streak: counted success"
FAILURE_STREAK_IGNORED_MARKER = "Failure streak: ignored run"


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
        self.fmkorea = None
        self.game_news = None
        self.runner_setup = None
        self.game_news_runner_setup = None
        if IS_PRIVATE_SOURCE:
            self.fmkorea = (
                ACTIVE_PRIVATE_WORKFLOWS / "scan-fmkorea.yml"
            ).read_text(encoding="utf-8")
            self.game_news = (
                ACTIVE_PRIVATE_WORKFLOWS / "scan-game-news.yml"
            ).read_text(encoding="utf-8")
            self.runner_setup = (
                ROOT / "scripts" / "setup_fmkorea_runner.ps1"
            ).read_text(encoding="utf-8")
            self.game_news_runner_setup = (
                ROOT / "scripts" / "setup_game_news_runner.ps1"
            ).read_text(encoding="utf-8")

    def test_workflows_share_one_non_cancelling_concurrency_group(self) -> None:
        for workflow in (self.hot, self.backfill):
            self.assertRegex(workflow, r"(?m)^\s*group: scan-dcinside\s*$")
            self.assertRegex(workflow, r"(?m)^\s*cancel-in-progress: false\s*$")

    def test_daily_headroom_is_checked_before_schema_and_persisting_work(self) -> None:
        for workflow, profile in ((self.hot, "community-hot"),
                                  (self.backfill, "community-backfill"),
                                  (self.fmkorea, "fmkorea-hot")):
            if workflow is None:
                continue
            self.assertLess(workflow.index("-m crawler.jobs.check_d1_budget"),
                            workflow.index("-m crawler.jobs.check_schema"))
            self.assertIn(f"--profile {profile}", workflow)
            self.assertIn('TC_D1_DAILY_GATE_ENABLED: "1"', workflow)
            self.assertIn("secrets.TC_CF_D1_ANALYTICS_TOKEN", workflow)
        if self.game_news is not None:
            self.assertLess(self.game_news.index("-m crawler.jobs.check_d1_budget"),
                            self.game_news.index("-m game_news.schema_check"))
            self.assertIn("--profile game-news --profile topic", self.game_news)
            self.assertIn("steps.d1_headroom.outcome == 'success'", self.game_news)
            self.assertIn("steps.d1_schema.outcome == 'success'", self.game_news)
            self.assertIn("steps.curation.outputs.d1_stop != 'true'", self.game_news)

    def test_workflows_pin_actions_and_do_not_persist_checkout_credentials(self) -> None:
        workflows = [self.hot, self.backfill]
        if self.fmkorea is not None:
            workflows.append(self.fmkorea)
        if self.game_news is not None:
            workflows.append(self.game_news)
        for workflow in workflows:
            self.assertIn(CHECKOUT_PIN, workflow)
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
        for workflow in (self.hot, self.backfill):
            self.assertIn(SETUP_PYTHON_PIN, workflow)

    def test_public_workflows_keep_minimal_permissions_and_safe_triggers(self) -> None:
        for workflow in (self.hot, self.backfill):
            self.assertRegex(
                workflow,
                r"(?m)^permissions:\s*\n"
                r"\s+actions: read\s*\n"
                r"\s+contents: read\s*$",
            )
            self.assertNotIn("pull_request_target:", workflow)
            self.assertNotRegex(workflow, r"(?m)^\s*[A-Za-z_-]+:\s*write\s*$")

        for workflow in (self.deploy_pages, self.deploy_scheduler):
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

        if self.fmkorea is not None:
            self.assertRegex(
                self.fmkorea,
                r"(?m)^permissions:\s*\n"
                r"\s+actions: read\s*\n"
                r"\s+contents: read\s*$",
            )
            self.assertNotIn("pull_request", self.fmkorea)
            self.assertNotIn("environment:", self.fmkorea)
            self.assertNotRegex(
                self.fmkorea,
                r"(?m)^\s*[A-Za-z_-]+:\s*write\s*$",
            )

        if self.game_news is not None:
            self.assertRegex(
                self.game_news,
                r"(?m)^permissions:\s*\n\s+contents: read\s*$",
            )
            self.assertNotIn("pull_request", self.game_news)
            self.assertNotIn("environment:", self.game_news)
            self.assertNotRegex(
                self.game_news,
                r"(?m)^\s*[A-Za-z_-]+:\s*write\s*$",
            )

    def test_collection_workflows_use_independent_three_failure_gates(self) -> None:
        expected = (
            (
                self.hot,
                "hot",
                "DC Hot crawl attempt",
                "DC Hot failure streak gate",
                "DC Hot",
            ),
            (
                self.backfill,
                "backfill",
                "DC Backfill crawl attempt",
                "DC Backfill failure streak gate",
                "DC Backfill",
            ),
        )
        for workflow, attempt_id, attempt_name, gate_name, lane_name in expected:
            self.assertRegex(
                workflow,
                rf"(?m)^\s{{2}}{attempt_id}:\s*$",
            )
            self.assertRegex(
                workflow,
                rf"(?m)^\s{{4}}name: {re.escape(attempt_name)}\s*$",
            )
            self.assertRegex(
                workflow,
                r"(?m)^\s{4}continue-on-error: true\s*$",
            )
            self.assertEqual(
                workflow.count(f'- name: "{FAILURE_STREAK_SUCCESS_MARKER}"'),
                1,
            )
            self.assertRegex(
                workflow,
                r"(?m)^\s{2}failure-streak-gate:\s*$",
            )
            self.assertRegex(
                workflow,
                rf"(?m)^\s{{4}}name: {re.escape(gate_name)}\s*$",
            )
            self.assertRegex(workflow, r"(?m)^\s{4}if: always\(\)\s*$")
            self.assertRegex(
                workflow,
                rf"(?m)^\s{{4}}needs: {attempt_id}\s*$",
            )
            self.assertIn(
                f"TC_FAILURE_STREAK_ATTEMPT_JOB: {attempt_name}",
                workflow,
            )
            self.assertIn(
                f"TC_FAILURE_STREAK_GATE_JOB: {gate_name}",
                workflow,
            )
            self.assertIn(
                f"TC_FAILURE_STREAK_LANE: {lane_name}",
                workflow,
            )
            self.assertIn(
                "python3 -m crawler.jobs.enforce_failure_streak",
                workflow,
            )
            gate = workflow.split("  failure-streak-gate:", maxsplit=1)[1]
            self.assertIn("runs-on: ubuntu-latest", gate)
            self.assertIn(CHECKOUT_PIN, gate)
            self.assertNotIn("actions/setup-python", gate)
            self.assertNotIn("TC_CF_API_TOKEN", gate)

    def test_deployment_workflows_use_locked_wrangler_and_split_tokens(self) -> None:
        for workflow in (self.deploy_pages, self.deploy_scheduler):
            self.assertIn(CHECKOUT_PIN, workflow)
            self.assertIn(SETUP_NODE_PIN, workflow)
            self.assertIn('node-version: "24"', workflow)
            self.assertIn("cache: npm", workflow)
            self.assertIn("npm ci --ignore-scripts", workflow)
            self.assertIn("run: npm test", workflow)
            self.assertIn("./node_modules/.bin/wrangler", workflow)
            self.assertNotIn("npx ", workflow)
            self.assertLess(
                workflow.index("actions/setup-node@"),
                workflow.index("npm ci --ignore-scripts"),
            )
            for action in re.findall(r"(?m)^\s*uses:\s*([^\s]+)", workflow):
                self.assertRegex(
                    action,
                    r"^[^@]+@[0-9a-f]{40}$",
                    msg=f"workflow action is not pinned to a full commit: {action}",
                )

        self.assertIn("secrets.CLOUDFLARE_PAGES_API_TOKEN", self.deploy_pages)
        self.assertNotIn("secrets.CLOUDFLARE_WORKERS_API_TOKEN", self.deploy_pages)
        self.assertIn(
            "secrets.CLOUDFLARE_SCHEDULER_API_TOKEN", self.deploy_scheduler
        )
        self.assertNotIn("secrets.CLOUDFLARE_PAGES_API_TOKEN", self.deploy_scheduler)
        self.assertNotIn("secrets.CLOUDFLARE_WORKERS_API_TOKEN", self.deploy_scheduler)

        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(package["engines"]["node"], ">=22.0.0")
        self.assertEqual(package["scripts"]["test"], 'node --test "tests/*.test.mjs"')
        self.assertIn("wrangler pages functions build", package["scripts"]["verify:pages"])
        self.assertIn("wrangler deploy --dry-run", package["scripts"]["verify:scheduler"])
        self.assertEqual(package["devDependencies"]["wrangler"], "4.124.0")
        self.assertEqual(lock["packages"][""]["engines"]["node"], ">=22.0.0")
        self.assertEqual(lock["packages"][""]["devDependencies"]["wrangler"], "4.124.0")

    def test_hot_dispatch_and_budget_contract(self) -> None:
        self.assertIn("workflow_dispatch:", self.hot)
        self.assertNotRegex(self.hot, r"(?m)^\s*schedule:\s*$")
        self.assertIn('TC_BLOCK_COOLDOWN_HOURS: "6"', self.hot)
        self.assertRegex(
            self.hot,
            r"(?ms)^\s{6}hot_lookback_minutes:\s*$.*?"
            r"^\s{8}default: 180\s*$.*?^\s{8}type: number\s*$",
        )
        self.assertRegex(
            self.hot,
            r"(?ms)^\s{6}hot_source_minutes:\s*$.*?"
            r"^\s{8}default: 3\s*$.*?^\s{8}type: number\s*$",
        )
        self.assertIn(
            "HOT_LOOKBACK_MINUTES: ${{ inputs.hot_lookback_minutes }}",
            self.hot,
        )
        self.assertIn(
            "HOT_SOURCE_MINUTES: ${{ inputs.hot_source_minutes }}",
            self.hot,
        )
        self.assertIn(
            'integer_input("HOT_LOOKBACK_MINUTES", 15, 1440)',
            self.hot,
        )
        self.assertIn('integer_input("HOT_SOURCE_MINUTES", 1, 10)', self.hot)
        self.assertIn("TC_HOT_MAX_SECONDS={source_minutes * 60}", self.hot)
        self.assertIn("TC_CYCLE_MAX_SECONDS={source_minutes * 60}", self.hot)
        self.assertRegex(self.hot, r"(?m)^\s{4}timeout-minutes: 30\s*$")
        self.assertIn('TC_DEEP_RESERVED_SECONDS: "0"', self.hot)
        self.assertIn(
            "python -m crawler.jobs.run_all_sources --mode hot --persist",
            self.hot,
        )
        self.assertIn("check_schema", self.hot)

    def test_hosted_hot_remains_dc_only_after_fmkorea_browser_block(self) -> None:
        self.assertNotIn("fmkorea_browser_smoke", self.hot)
        self.assertNotIn("run_fmkorea_sources", self.hot)
        self.assertNotIn("requirements-fmkorea-browser", self.hot)
        self.assertNotIn("playwright", self.hot.lower())

    def test_backfill_dispatch_and_budget_contract(self) -> None:
        self.assertIn("workflow_dispatch:", self.backfill)
        self.assertNotRegex(self.backfill, r"(?m)^\s*schedule:\s*$")
        self.assertIn('TC_BLOCK_COOLDOWN_HOURS: "6"', self.backfill)
        self.assertIn('TC_CYCLE_MAX_SECONDS: "600"', self.backfill)
        self.assertIn('TC_DEEP_RESERVED_SECONDS: "300"', self.backfill)
        self.assertRegex(self.backfill, r"(?m)^\s{4}timeout-minutes: 45\s*$")
        self.assertIn(
            "python -m crawler.jobs.run_all_sources --mode backfill --persist",
            self.backfill,
        )
        self.assertIn("check_schema", self.backfill)

    def test_private_ops_script_supports_one_run_hot_lookback_override(self) -> None:
        if not IS_PRIVATE_SOURCE:
            self.skipTest("private operations script is not exported")

        script = (ROOT / "scripts" / "manage_crawl_workflow.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn("[ValidateRange(15, 1440)]", script)
        self.assertIn("[ValidateRange(1, 10)]", script)
        self.assertIn(
            '$PSBoundParameters.ContainsKey(\n    "HotLookbackMinutes"\n)',
            script,
        )
        self.assertIn(
            "hot_lookback_minutes = [string]$effectiveHotLookbackMinutes",
            script,
        )
        self.assertIn(
            "hot_source_minutes = [string]$effectiveHotSourceMinutes",
            script,
        )
        self.assertIn("[Math]::Ceiling($HotLookbackMinutes / 60.0)", script)
        self.assertIn("$dispatchBody.inputs", script)
        self.assertIn('ConvertTo-Json -Depth 4 -Compress', script)
        self.assertIn(
            '"scan-fmkorea.yml", "scan-game-news.yml")]',
            script,
        )
        self.assertIn(
            "An active Hot or Backfill run already exists",
            script,
        )

        self.assertIn('$Repository = "Chimnii/TodayCommunity"', script)
        self.assertIn("[switch]$FmkoreaPersist", script)
        self.assertIn("[ValidateRange(0, 30)]", script)
        self.assertIn("dispatched_at = [DateTimeOffset]::UtcNow", script)
        self.assertIn("max_pages_per_target = [string]$FmkoreaMaxPages", script)
        self.assertIn("[switch]$GameNewsPersist", script)
        self.assertIn('$isGameNewsWorkflow = $Workflow -eq "scan-game-news.yml"', script)
        self.assertIn("persist = $GameNewsPersist.IsPresent", script)

    def test_pages_deploys_only_relevant_main_changes_after_verification(self) -> None:
        self.assertIn("workflow_dispatch:", self.deploy_pages)
        self.assertRegex(self.deploy_pages, r"(?m)^  push:\s*$")
        self.assertRegex(self.deploy_pages, r"(?m)^    branches:\s*\n      - main\s*$")
        for path in (
            '"dashboard/**"',
            '"functions/**"',
            '"package.json"',
            '"package-lock.json"',
            '".github/workflows/deploy-pages.yml"',
        ):
            self.assertIn(path, self.deploy_pages)
        self.assertNotRegex(self.deploy_pages, r"(?m)^\s*schedule:\s*$")
        self.assertIn("if: github.ref == 'refs/heads/main'", self.deploy_pages)
        self.assertIn("npm run verify:pages", self.deploy_pages)
        self.assertLess(
            self.deploy_pages.index("run: npm test"),
            self.deploy_pages.index("wrangler pages deploy"),
        )
        self.assertLess(
            self.deploy_pages.index("run: npm run verify:pages"),
            self.deploy_pages.index("wrangler pages deploy"),
        )

    def test_scheduler_deploys_only_relevant_main_changes_after_verification(self) -> None:
        self.assertIn("workflow_dispatch:", self.deploy_scheduler)
        self.assertRegex(self.deploy_scheduler, r"(?m)^  push:\s*$")
        self.assertRegex(self.deploy_scheduler, r"(?m)^    branches:\s*\n      - main\s*$")
        for path in (
            '"scheduler/**"',
            '"package.json"',
            '"package-lock.json"',
            '".github/workflows/deploy-scheduler.yml"',
        ):
            self.assertIn(path, self.deploy_scheduler)
        self.assertNotRegex(self.deploy_scheduler, r"(?m)^\s*schedule:\s*$")
        self.assertIn("if: github.ref == 'refs/heads/main'", self.deploy_scheduler)
        self.assertIn("npm run verify:scheduler", self.deploy_scheduler)
        self.assertLess(
            self.deploy_scheduler.index("run: npm test"),
            self.deploy_scheduler.index("wrangler deploy --config"),
        )
        self.assertLess(
            self.deploy_scheduler.index("run: npm run verify:scheduler"),
            self.deploy_scheduler.index("wrangler deploy --config"),
        )

    def test_no_scheduled_production_workflow_runs_the_combined_mode(self) -> None:
        for path in WORKFLOWS.glob("*.yml"):
            workflow = path.read_text(encoding="utf-8")
            if re.search(r"(?m)^\s*schedule:\s*$", workflow):
                self.assertNotRegex(
                    workflow,
                    r"python -m crawler\.jobs\.run_cycle(?![^\n]*--mode)",
                    msg=f"{path.name} still schedules the combined crawl mode",
                )

    def test_private_source_has_exactly_the_isolated_private_workflows(self) -> None:
        if not IS_PRIVATE_SOURCE:
            self.skipTest("running in the public mirror")

        workflow_names = {
            path.name for path in ACTIVE_PRIVATE_WORKFLOWS.glob("*.yml")
        }
        self.assertEqual(
            workflow_names,
            {"scan-fmkorea.yml", "scan-game-news.yml"},
        )

    def test_private_fmkorea_workflow_is_hot_only_and_self_hosted(self) -> None:
        if self.fmkorea is None:
            self.skipTest("running in the public mirror")

        workflow = self.fmkorea
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s*schedule:\s*$")
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)
        self.assertIn(
            "runs-on: [self-hosted, Windows, X64, todaycommunity-fm]",
            workflow,
        )
        self.assertRegex(workflow, r"(?m)^\s+group: scan-fmkorea\s*$")
        self.assertRegex(workflow, r"(?m)^\s+cancel-in-progress: false\s*$")
        self.assertRegex(workflow, r"(?m)^\s{4}timeout-minutes: 20\s*$")
        self.assertIn(
            "shell: powershell -NoLogo -NoProfile -NonInteractive "
            "-ExecutionPolicy Bypass -File {0}",
            workflow,
        )
        self.assertNotRegex(workflow, r"(?m)^\s+shell: powershell\s*$")
        self.assertIn(
            r"TC_FMKOREA_PYTHON: C:\ProgramData\TodayCommunity\fmkorea-venv\Scripts\python.exe",
            workflow,
        )
        self.assertIn(
            r"TC_FMKOREA_PROFILE_DIR: C:\ProgramData\TodayCommunity\fmkorea-chrome-profile",
            workflow,
        )
        self.assertIn('TC_FMKOREA_CDP_PORT: "39225"', workflow)
        self.assertIn('TC_FMKOREA_HEADLESS: "1"', workflow)
        self.assertIn('TC_FMKOREA_REQUEST_INTERVAL_SECONDS: "10"', workflow)
        self.assertIn("crawler/requirements-fmkorea-browser.txt", workflow)
        self.assertIn("--mode", workflow)
        self.assertIn('"hot"', workflow)
        self.assertNotIn('"backfill"', workflow)
        self.assertIn('"--persist"', workflow)
        self.assertNotIn("playwright install", workflow)

    def test_private_fmkorea_failure_gate_uses_the_same_free_runner(self) -> None:
        if self.fmkorea is None:
            self.skipTest("running in the public mirror")

        workflow = self.fmkorea
        self.assertRegex(
            workflow,
            r"(?m)^\s{4}name: FM Hot crawl attempt\s*$",
        )
        self.assertRegex(
            workflow,
            r"(?m)^\s{4}continue-on-error: true\s*$",
        )
        self.assertEqual(
            workflow.count(
                "runs-on: [self-hosted, Windows, X64, todaycommunity-fm]"
            ),
            2,
        )
        self.assertEqual(
            workflow.count(f'- name: "{FAILURE_STREAK_SUCCESS_MARKER}"'),
            1,
        )
        self.assertEqual(
            workflow.count(f'- name: "{FAILURE_STREAK_IGNORED_MARKER}"'),
            1,
        )
        self.assertIn(
            "steps.freshness.outcome == 'success' &&",
            workflow,
        )
        self.assertIn(
            "steps.freshness.outputs.should_run != 'true'",
            workflow,
        )
        self.assertRegex(
            workflow,
            r"(?m)^\s{2}failure-streak-gate:\s*$",
        )
        gate = workflow.split("  failure-streak-gate:", maxsplit=1)[1]
        self.assertIn("name: FM Hot failure streak gate", gate)
        self.assertIn("if: always()", gate)
        self.assertIn("needs: hot", gate)
        self.assertIn(GITHUB_SCRIPT_PIN, gate)
        self.assertEqual(
            re.findall(r"(?m)^\s{6}- name:", gate),
            ["      - name:"],
        )
        self.assertEqual(
            re.findall(r"(?m)^\s+uses:\s*([^\s]+)", gate),
            [GITHUB_SCRIPT_PIN.split(" ", maxsplit=1)[0]],
        )
        self.assertIn("github-token: ${{ github.token }}", gate)
        self.assertIn("retries: 3", gate)
        self.assertIn("listJobsForWorkflowRunAttempt", gate)
        self.assertIn("completeJobPage", gate)
        self.assertIn("jobs.length !== totalCount", gate)
        self.assertNotIn("github.paginate(", gate)
        self.assertIn("listWorkflowRuns", gate)
        self.assertIn("GITHUB_RUN_ATTEMPT", gate)
        self.assertIn("currentIsOutOfOrderRerun", gate)
        self.assertIn("Ignoring out-of-order rerun", gate)
        self.assertIn("attemptNumber > 1 &&", gate)
        self.assertIn('attempts[0].status !== "completed"', gate)
        self.assertNotIn('status: "completed"', gate)
        self.assertIn("started_at", gate)
        self.assertIn("created_at", gate)
        self.assertIn('const attemptJobName = "FM Hot crawl attempt";', gate)
        self.assertIn('const gateJobName = "FM Hot failure streak gate";', gate)
        self.assertNotIn("actions/checkout", gate)
        self.assertNotIn("actions/setup-python", gate)
        self.assertNotIn("TC_FMKOREA_PYTHON", gate)
        self.assertNotIn("crawler.jobs.enforce_failure_streak", gate)
        self.assertNotIn("TC_CF_ACCOUNT_ID", gate)
        self.assertNotIn("TC_CF_DATABASE_ID", gate)
        self.assertNotIn("TC_CF_API_TOKEN", gate)

    def test_private_fmkorea_workflow_fails_safe_before_checkout(self) -> None:
        if self.fmkorea is None:
            self.skipTest("running in the public mirror")

        workflow = self.fmkorea
        freshness_index = workflow.index("Validate freshness and inputs before checkout")
        checkout_index = workflow.index("Checkout approved main revision")
        self.assertLess(freshness_index, checkout_index)
        self.assertIn("$maximumAgeMinutes = 45", workflow)
        self.assertIn("$maximumFutureSkewMinutes = 5", workflow)
        self.assertIn("$shouldRun = $false", workflow)
        self.assertIn("PERSIST_REQUESTED: ${{ inputs.persist }}", workflow)
        self.assertIn(
            "Persisting dispatches require dispatched_at for stale-job protection.",
            workflow,
        )
        self.assertIn(
            "Untimestamped manual dispatches require an explicit page limit.",
            workflow,
        )
        self.assertIn('"should_run=$($shouldRun.ToString()', workflow)
        self.assertIn(
            "Out-File -FilePath $env:GITHUB_OUTPUT -Encoding utf8 -Append",
            workflow,
        )
        self.assertNotIn(">> $env:GITHUB_OUTPUT", workflow)
        self.assertIn("max_pages_per_target", workflow)
        self.assertIn("default: false", workflow)
        self.assertIn("default: 1", workflow)
        self.assertGreaterEqual(
            workflow.count("if: steps.freshness.outputs.should_run == 'true'"),
            6,
        )
        self.assertIn("inputs.persist == true", workflow)
        self.assertIn("inputs.persist != true", workflow)
        for secret_name in (
            "TC_CF_ACCOUNT_ID",
            "TC_CF_DATABASE_ID",
            "TC_CF_API_TOKEN",
        ):
            self.assertIn(f"secrets.{secret_name}", workflow)

    def test_private_game_news_workflow_is_isolated_and_headless(self) -> None:
        if self.game_news is None:
            self.skipTest("running in the public mirror")

        workflow = self.game_news
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s*schedule:\s*$")
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)
        self.assertIn(
            "runs-on: [self-hosted, Windows, X64, todaycommunity-game-news]",
            workflow,
        )
        self.assertRegex(workflow, r"(?m)^\s+group: scan-game-news\s*$")
        self.assertRegex(workflow, r"(?m)^\s+cancel-in-progress: false\s*$")
        self.assertRegex(workflow, r"(?m)^\s{4}timeout-minutes: 60\s*$")
        self.assertIn(
            "shell: powershell -NoLogo -NoProfile -NonInteractive "
            "-ExecutionPolicy Bypass -File {0}",
            workflow,
        )
        self.assertEqual(workflow.count('TC_GAME_NEWS_HEADLESS: "1"'), 2)
        self.assertNotIn('TC_GAME_NEWS_HEADLESS: "0"', workflow)
        self.assertNotIn("TC_FMKOREA_", workflow)
        self.assertNotIn(r"C:\ProgramData\TodayCommunity", workflow)
        self.assertNotIn("playwright install", workflow)

    def test_private_game_news_workflow_fails_closed_before_checkout(self) -> None:
        if self.game_news is None:
            self.skipTest("running in the public mirror")

        workflow = self.game_news
        freshness_index = workflow.index("Validate freshness and inputs before checkout")
        identity_index = workflow.index("Verify dedicated runner account")
        checkout_index = workflow.index("Checkout approved main revision")
        self.assertLess(freshness_index, identity_index)
        self.assertLess(identity_index, checkout_index)
        self.assertIn("$maximumAgeMinutes = 45", workflow)
        self.assertIn("$maximumFutureSkewMinutes = 5", workflow)
        self.assertIn("$shouldRun = $false", workflow)
        self.assertIn("PERSIST_REQUESTED: ${{ inputs.persist }}", workflow)
        self.assertIn(
            "Persisting dispatches require dispatched_at for stale-job protection.",
            workflow,
        )
        self.assertIn('default: false', workflow)
        self.assertIn('$accountLeaf.Equals("user"', workflow)
        self.assertIn('$expectedIdentity = "CHIMNII-MAIN\\user"', workflow)
        self.assertIn('"CHIMNII-MAIN"', workflow)
        self.assertNotIn("$env:USERNAME", workflow)
        self.assertNotIn(">> $env:GITHUB_OUTPUT", workflow)
        self.assertGreaterEqual(
            workflow.count("steps.freshness.outputs.should_run == 'true'"),
            9,
        )

    def test_private_game_news_workflow_uses_only_dedicated_oauth_runtime(self) -> None:
        if self.game_news is None:
            self.skipTest("running in the public mirror")

        workflow = self.game_news
        for path_name in (
            "game-news-venv",
            "game-news-chrome-profile",
            "game-news-codex-home",
            "game-news-runs",
            "game-news-logs",
        ):
            self.assertIn(path_name, workflow)
        self.assertIn(".todaycommunity-game-news-codex-command", workflow)
        self.assertIn("[IO.File]::ReadAllText($codexMarker)", workflow)
        self.assertIn('[IO.Path]::GetFileName($codexPath) -ne "codex.cmd"', workflow)
        self.assertNotIn("login status", workflow)
        self.assertNotIn('"Logged in using ChatGPT"', workflow)
        self.assertIn("game_news.runner", workflow)
        self.assertIn("$env:OPENAI_API_KEY = $null", workflow)
        self.assertIn("TC_GAME_NEWS_CODEX_HOME", workflow)
        runtime = (ROOT / "game_news" / "codex_exec.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('cli_auth_credentials_store=\"keyring\"', runtime)
        self.assertIn('Join-Path $codexHome "auth.json"', workflow)
        self.assertIn("File-based Codex credentials are forbidden", workflow)
        self.assertNotIn("upload-artifact", workflow)
        self.assertNotIn("actions/upload-artifact", workflow)

    def test_private_game_news_workflow_installs_tracked_requirements_and_runs_contract_entrypoint(self) -> None:
        if self.game_news is None:
            self.skipTest("running in the public mirror")

        workflow = self.game_news
        self.assertIn("game_news/requirements.txt", workflow)
        self.assertIn("git ls-files --error-unmatch", workflow)
        self.assertIn("--requirement $requirements", workflow)
        self.assertIn("-m pip check", workflow)
        schema_index = workflow.index("-m game_news.schema_check")
        self.assertIn(
            "-m game_news.schema_check --bootstrap-sources",
            workflow,
        )
        persist_index = workflow.index(
            "-m game_news.runner `\n            --run-root",
            schema_index,
        )
        self.assertLess(schema_index, persist_index)
        self.assertEqual(workflow.count("-m game_news.runner"), 2)
        topic_index = workflow.index("-m community_topics.runner", persist_index)
        self.assertGreater(topic_index, persist_index)
        self.assertEqual(workflow.count("-m community_topics.runner"), 1)
        self.assertEqual(workflow.count("--run-root"), 3)
        self.assertIn('--run-root "${{ steps.run_root.outputs.path }}" --persist', workflow)
        self.assertIn('"${{ steps.run_root.outputs.path }}" "community-topics"', workflow)
        self.assertIn("--run-root $topicRunRoot --persist", workflow)
        self.assertIn("inputs.persist == true", workflow)
        self.assertIn("inputs.persist != true", workflow)

        topic_step = workflow.split(
            "- name: Refresh stored community topic snapshots",
            maxsplit=1,
        )[1].split("- name: Remove non-persisting run root", maxsplit=1)[0]
        self.assertIn("always()", topic_step)
        self.assertIn("steps.run_root.outcome == 'success'", topic_step)
        self.assertIn("$env:OPENAI_API_KEY = $null", topic_step)
        self.assertIn("$env:CODEX_API_KEY = $null", topic_step)
        self.assertIn("$env:CODEX_HOME = $env:TC_GAME_NEWS_CODEX_HOME", topic_step)

    def test_private_game_news_workflow_limits_d1_secrets_and_runtime_retention(self) -> None:
        if self.game_news is None:
            self.skipTest("running in the public mirror")

        workflow = self.game_news
        nonpersist = workflow.split(
            "- name: Run non-persisting game-news curation",
            maxsplit=1,
        )[1].split("- name: Run persisting game-news curation", maxsplit=1)[0]
        self.assertNotIn("secrets.", nonpersist)
        for secret_name in (
            "TC_CF_ACCOUNT_ID",
            "TC_CF_DATABASE_ID",
            "TC_CF_API_TOKEN",
        ):
            expected = 4 if secret_name == "TC_CF_ACCOUNT_ID" else 3
            self.assertEqual(workflow.count(f"secrets.{secret_name}"), expected)
        self.assertIn("Apply 30-day dedicated runtime retention", workflow)
        self.assertIn("[DateTime]::UtcNow.AddDays(-30)", workflow)
        self.assertIn('.todaycommunity-game-news-owned', workflow)
        self.assertIn("Remove non-persisting run root", workflow)
        self.assertIn('"TodayCommunity-game-news-"', workflow)

    def test_private_runner_setup_uses_verified_service_install(self) -> None:
        if self.runner_setup is None:
            self.skipTest("running in the public mirror")

        script = self.runner_setup
        self.assertIn('"C:\\actions-runner\\todaycommunity-fm"', script)
        self.assertIn('"C:\\ProgramData\\TodayCommunity"', script)
        self.assertIn('"Chimnii/TodayCommunity"', script)
        self.assertIn('$RunnerLabel = "todaycommunity-fm"', script)
        self.assertIn('sha256_checksum', script)
        self.assertIn('Get-FileHash -LiteralPath $archive -Algorithm SHA256', script)
        self.assertIn('--runasservice', script)
        self.assertIn('"NT AUTHORITY\\NETWORK SERVICE"', script)
        self.assertIn('"S-1-5-20"', script)
        self.assertIn('"S-1-5-18"', script)
        self.assertIn('"S-1-5-32-544"', script)
        self.assertIn('$acl.SetAccessRuleProtection($true, $false)', script)
        self.assertIn('$acl.SetOwner($administrators)', script)
        self.assertIn('function Assert-RestrictedRoot', script)
        self.assertIn('$acl.GetAccessRules(', script)
        self.assertIn('Restricted directory contains an unexpected ACL entry', script)
        self.assertIn('[IO.Directory]::Move($pythonStage, $PythonRoot)', script)
        self.assertIn('[IO.Directory]::Move($venvStage, $VenvRoot)', script)
        self.assertIn('[IO.Directory]::Move($runnerStage, $RunnerRoot)', script)
        self.assertIn('Remove-StagingDirectory', script)
        self.assertIn('Remove-Item -LiteralPath $archive -Force', script)
        self.assertNotIn('-m pip install', script)
        self.assertIn('RunnerRoot must be a child of $allowedRunnerParent.', script)
        self.assertIn('RuntimeRoot must be exactly $allowedRuntime.', script)
        self.assertIn('.todaycommunity-runner-package.json', script)
        self.assertIn('Get-GitHubToken', script)
        self.assertIn(
            '$downloads = Invoke-GitHubApi -Method Get -Uri $downloadsUri',
            script,
        )
        self.assertIn('$application = $downloads |', script)
        self.assertNotIn(
            '$application = Invoke-GitHubApi -Method Get -Uri $downloadsUri |',
            script,
        )
        self.assertNotIn('--replace', script)
        self.assertNotIn('--disableupdate', script)

    def test_private_runner_rejects_duplicate_required_acl_entries(self) -> None:
        if self.runner_setup is None:
            self.skipTest("running in the public mirror")

        script = self.runner_setup
        validator = script.split("function Assert-RestrictedRoot", 1)[1].split(
            "function Assert-InstallInputs", 1
        )[0]
        self.assertIn("$seenRequiredSids", validator)
        self.assertIn("$seenRequiredSids.ContainsKey($sid)", validator)
        self.assertIn("$seenRequiredSids.Count -ne $expected.Count", validator)
        self.assertNotIn("AddAccessRule", validator)
        self.assertNotIn("OptionalNonInheritedListDirectoryIdentity", script)

    def test_game_news_runner_setup_uses_windowless_per_user_task(self) -> None:
        if self.game_news_runner_setup is None:
            self.skipTest("running in the public mirror")

        script = self.game_news_runner_setup
        self.assertIn(
            '"$env:LOCALAPPDATA\\TodayCommunity\\game-news-runner"',
            script,
        )
        self.assertIn('$RunnerLabel = "todaycommunity-game-news"', script)
        self.assertIn('$ExpectedRepository = "Chimnii/TodayCommunity"', script)
        self.assertIn('$ExpectedComputerName = "CHIMNII-MAIN"', script)
        self.assertIn('$ExpectedAccountLeaf = "user"', script)
        self.assertIn('$RunnerAccount = "CHIMNII-MAIN\\user"', script)
        self.assertIn(
            '$RunnerName = "todaycommunity-game-news-CHIMNII-MAIN"',
            script,
        )
        for path_name in (
            "game-news-runtime",
            "game-news-venv",
            "game-news-chrome-profile",
            "game-news-codex-home",
            "game-news-runs",
            "game-news-logs",
            "game-news-runner",
        ):
            self.assertIn(path_name, script)
        self.assertIn("Game-news runtime paths must not be shared.", script)
        self.assertNotIn("C:\\actions-runner", script)
        self.assertNotIn("requires an elevated Windows PowerShell", script)
        self.assertIn("$item.SetAccessControl($acl)", script)
        self.assertNotIn("Set-Acl -LiteralPath $item.FullName", script)

        self.assertIn('$ScheduledTaskPath = "\\"', script)
        self.assertIn(
            '$ScheduledTaskName = "TodayCommunity Game News Runner"',
            script,
        )
        self.assertIn("windowless background per-user scheduled task", script)
        self.assertNotIn('New-Object -ComObject "Schedule.Service"', script)
        self.assertNotIn("CreateFolder", script)
        self.assertIn("New-ScheduledTaskAction", script)
        self.assertIn("-WindowStyle Hidden", script)
        self.assertIn("New-ScheduledTaskTrigger -AtLogOn -User $RunnerAccount", script)
        self.assertIn("New-ScheduledTaskPrincipal -UserId $RunnerAccount", script)
        self.assertIn("-LogonType Interactive -RunLevel Limited", script)
        self.assertIn("New-ScheduledTaskSettingsSet", script)
        self.assertIn("-Compatibility Win8", script)
        self.assertIn("-MultipleInstances IgnoreNew", script)
        self.assertIn("-RestartCount 3", script)
        self.assertIn("-RestartInterval (New-TimeSpan -Minutes 1)", script)
        self.assertIn("-ExecutionTimeLimit ([TimeSpan]::Zero)", script)
        self.assertIn("-AllowStartIfOnBatteries", script)
        self.assertIn("-DontStopIfGoingOnBatteries", script)
        self.assertIn("-StartWhenAvailable", script)
        self.assertIn('$task.Settings.Compatibility -ne "Win8"', script)
        self.assertIn("$task.Settings.Hidden", script)
        settings_block = script.split(
            "$settings = New-ScheduledTaskSettingsSet", 1
        )[1].split("Register-ScheduledTask", 1)[0]
        self.assertNotIn("-Hidden", settings_block)
        self.assertIn("Start-ScheduledTask -TaskPath $ScheduledTaskPath", script)
        install_tail = script.split("    Assert-InstallInputs\n", 1)[1]
        install_steps = (
            "Stop-RunnerScheduledTaskForMaintenance",
            "Install-PythonRuntime",
            "Install-RunnerApplication",
            "Write-RunnerLauncher",
            "Install-RunnerScheduledTask",
            "Start-RunnerScheduledTask",
            "$deadline = [DateTimeOffset]::UtcNow.AddSeconds(30)",
        )
        self.assertEqual(
            [install_tail.index(step) for step in install_steps],
            sorted(install_tail.index(step) for step in install_steps),
        )

        launcher = script.split("function Write-RunnerLauncher", 1)[1].split(
            "function Get-DedicatedUserSid", 1
        )[0]
        self.assertIn('$runner = Join-Path $actualRoot "run.cmd"', launcher)
        self.assertIn("& $runner", launcher)
        self.assertIn("exit $LASTEXITCODE", launcher)
        self.assertNotIn("Start-Process", launcher)

        self.assertIn("function Stop-RunnerScheduledTaskForMaintenance", script)
        maintenance = script.split(
            "function Stop-RunnerScheduledTaskForMaintenance", 1
        )[1].split("function Assert-InstallInputs", 1)[0]
        self.assertIn("$remote.busy", maintenance)
        self.assertIn("Stop-ScheduledTask", maintenance)
        self.assertIn('$task.State -ne "Running"', maintenance)
        self.assertIn(
            "$restartExistingTask = Stop-RunnerScheduledTaskForMaintenance",
            script,
        )
        self.assertIn("if ($restartExistingTask)", script)

        self.assertIn("Get-Command codex.cmd -CommandType Application", script)
        self.assertIn('$RequiredCodexVersion = "0.152.1"', script)
        self.assertIn("function Assert-CodexVersion", script)
        self.assertIn("codex-cli $RequiredCodexVersion", script)
        self.assertIn("Write-CodexCommandMarker", script)
        self.assertIn(".todaycommunity-game-news-codex-command", script)
        self.assertIn("login --device-auth", script)
        device_login = script.split("function Invoke-CodexDeviceLogin", 1)[1].split(
            "function Show-Status", 1
        )[0]
        self.assertNotRegex(device_login, r"}\s*\|\s*Out-Null")
        self.assertIn("Follow the Codex device-auth instructions", device_login)
        self.assertLess(
            device_login.index("if (Test-CodexChatGPTLogin)"),
            device_login.index("login --device-auth"),
        )
        self.assertIn("already logged in using ChatGPT", device_login)
        self.assertIn("login status", script)
        self.assertIn('"Logged in using ChatGPT"', script)
        self.assertIn('$ErrorActionPreference = "Continue"', script)
        self.assertIn(
            "$ErrorActionPreference = $previousErrorActionPreference",
            script,
        )
        self.assertIn("$statusExitCode = $LASTEXITCODE", script)
        self.assertIn('"OPENAI_API_KEY"', script)
        self.assertIn('"CODEX_API_KEY"', script)
        self.assertIn('cli_auth_credentials_store=\"keyring\"', script)
        self.assertIn('Join-Path $CodexHome "auth.json"', script)
        self.assertIn("OS-keyring authentication is required", script)
        self.assertNotIn("Copy-Item -LiteralPath $CodexHome", script)

        self.assertIn("Get-GitHubToken", script)
        self.assertIn("registration-token", script)
        self.assertIn('repositoryMetadata.private', script)
        self.assertIn('repositoryMetadata.full_name', script)
        self.assertIn(
            'The game-news runner may only register to its expected private repository.',
            script,
        )
        self.assertIn("sha256_checksum", script)
        self.assertIn(
            "Get-FileHash -LiteralPath $archive `\n                -Algorithm SHA256",
            script,
        )
        self.assertIn("function Assert-ExistingRunnerRegistrationIsOwned", script)
        self.assertIn("function Assert-ExistingRunnerMatchesRemote", script)
        for required_runner_file in (
            '".runner"',
            '".credentials"',
            '".credentials_rsaparams"',
            '".todaycommunity-game-news-runner-package.json"',
            '"config.cmd"',
            '"run.cmd"',
        ):
            self.assertIn(required_runner_file, script)
        self.assertIn("$remoteMatches.Count -ne 1", script)
        self.assertIn("[long]$remote.id -ne [long]$settings.agentId", script)
        self.assertIn("$remoteLabels -notcontains $RunnerLabel", script)
        self.assertIn("$remoteWithDedicatedName", script)
        self.assertIn("if (Test-Path -LiteralPath $runnerConfig -PathType Leaf)", script)
        self.assertIn("Assert-ExistingRunnerMatchesRemote", script)
        self.assertIn("& $config --unattended --url $RepositoryUrl", script)
        runner_install = script.split("function Install-RunnerApplication", 1)[1].split(
            "if ($PSVersionTable.PSVersion.Major", 1
        )[0]
        self.assertLess(
            runner_install.index(
                "if (Test-Path -LiteralPath $runnerConfig -PathType Leaf)"
            ),
            runner_install.index("registration-token"),
        )
        for forbidden in (
            "--runasservice",
            "--windowslogonaccount",
            "--windowslogonpassword",
            "Get-Credential",
            "PSCredential",
            "SecureStringToBSTR",
            "ZeroFreeBSTR",
            "actions/runners/remove-token",
            "config remove",
        ):
            self.assertNotIn(forbidden, script)
        self.assertNotIn("--replace", script)
        self.assertNotIn("--disableupdate", script)

        self.assertIn("game_news\\requirements.txt", script)
        self.assertIn("ls-files --error-unmatch -- game_news/requirements.txt", script)
        self.assertIn("-m pip check", script)
        self.assertIn("[IO.Directory]::Move($pythonStage, $PythonRoot)", script)
        self.assertIn("[IO.Directory]::Move($venvStage, $VenvRoot)", script)
        self.assertIn("[IO.Directory]::Move($runnerStage, $RunnerRoot)", script)
        self.assertIn("Remove-StagingDirectory", script)
        self.assertIn("$acl.SetAccessRuleProtection($true, $false)", script)
        self.assertIn('"S-1-5-18"', script)
        self.assertIn('"S-1-5-32-544"', script)
        self.assertIn('.todaycommunity-game-news-profile', script)
        self.assertIn('TodayCommunity dedicated game-news Chrome profile', script)
        self.assertIn('[IO.File]::WriteAllText(', script)

    def test_public_mirror_keeps_agent_instructions_private(self) -> None:
        if not IS_PRIVATE_SOURCE:
            self.skipTest("running in the public mirror")

        script = (ROOT / "scripts" / "sync_public_mirror.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('"AGENTS.md"', script)
        self.assertIn('$path -match "^game_news/"', script)
        self.assertIn('$path -match "^community_topics/"', script)
        self.assertIn('"tests/test_community_topics.py"', script)
        self.assertIn('$path -ne "tests/test_community_topics.py"', script)

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
