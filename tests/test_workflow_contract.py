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
        self.runner_setup = None
        if IS_PRIVATE_SOURCE:
            self.fmkorea = (
                ACTIVE_PRIVATE_WORKFLOWS / "scan-fmkorea.yml"
            ).read_text(encoding="utf-8")
            self.runner_setup = (
                ROOT / "scripts" / "setup_fmkorea_runner.ps1"
            ).read_text(encoding="utf-8")

    def test_workflows_share_one_non_cancelling_concurrency_group(self) -> None:
        for workflow in (self.hot, self.backfill):
            self.assertRegex(workflow, r"(?m)^\s*group: scan-dcinside\s*$")
            self.assertRegex(workflow, r"(?m)^\s*cancel-in-progress: false\s*$")

    def test_workflows_pin_actions_and_do_not_persist_checkout_credentials(self) -> None:
        workflows = [self.hot, self.backfill]
        if self.fmkorea is not None:
            workflows.append(self.fmkorea)
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
            '"scan-dcinside-backfill.yml", "scan-fmkorea.yml")]',
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
        self.assertIn("node --test tests/scheduler_worker.test.mjs", self.deploy_scheduler)
        self.assertIn(
            "wrangler deploy --dry-run --config scheduler/wrangler.jsonc",
            self.deploy_scheduler,
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

    def test_private_source_has_exactly_one_fmkorea_crawl_workflow(self) -> None:
        if not IS_PRIVATE_SOURCE:
            self.skipTest("running in the public mirror")

        workflow_names = {
            path.name for path in ACTIVE_PRIVATE_WORKFLOWS.glob("*.yml")
        }
        self.assertEqual(workflow_names, {"scan-fmkorea.yml"})

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

    def test_public_mirror_keeps_agent_instructions_private(self) -> None:
        if not IS_PRIVATE_SOURCE:
            self.skipTest("running in the public mirror")

        script = (ROOT / "scripts" / "sync_public_mirror.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('"AGENTS.md"', script)

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
