"""Read-only, post-completion collection alert gate.

Raw jobs keep their failures. This workflow's exit status is an alert signal,
not a rewrite of the collector result. No mail, messenger, DB or issue writes.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from crawler.collection_health import COMMUNITY_BUDGET_CONTRACT, CONTRACT, PREFIX, validate_health
from crawler.jobs.enforce_failure_streak import GitHubActionsClient, GitHubApiError

LANES = {
    "scan-ai-practice.yml": ("ai", "Run AI practice curation", "AI practice curation"),
    "scan-game-news.yml": ("game", "Run game news curation", "Game news curation"),
    "scan-fmkorea.yml": ("fm", "Run FMKorea Hot crawl", "FM Hot crawl attempt"),
    "scan-dcinside.yml": ("dc", "Run DC Hot crawl", "DC Hot crawl attempt"),
}
OBSERVER = "collection-alerts.yml"
ALERT_STEP = "Notify collection incident"
FAILURES = {"failure", "timed_out", "cancelled", "startup_failure", "action_required", "stale"}
MAX_HISTORY = 1000


@dataclass
class Decision:
    lane: str
    health: dict[str, str] = field(default_factory=dict)
    streaks: dict[str, int] = field(default_factory=dict)
    alert_components: list[str] = field(default_factory=list)
    reason: str = "observed"
    suppression: str = ""
    history_boundary: str = ""

    @property
    def notify(self):
        return bool(self.alert_components) and not self.suppression


def validate_event(event: dict, repository: str) -> tuple[dict, tuple[str, str, str]]:
    run = event.get("workflow_run", {})
    filename = str(run.get("path", "")).removeprefix(".github/workflows/")
    lane = LANES.get(filename)
    # GitHub's run.name can be the dynamic run-name (including dispatch time),
    # not the workflow's static name. Trust its exact path and repository scope;
    # the workflow_run subscription separately selects static workflow names.
    if (event.get("action") != "completed" or lane is None
            or run.get("path") != f".github/workflows/{filename}"
            or run.get("head_branch") != "main" or run.get("event") != "workflow_dispatch"
            or run.get("status") != "completed"
            or run.get("repository", {}).get("full_name") != repository
            or run.get("head_repository", {}).get("full_name") != repository):
        raise ValueError("Untrusted or out-of-scope collection event")
    return run, lane


def classify(run: dict, jobs: list[dict], job_name: str) -> dict[str, str]:
    matches = [j for j in jobs if j.get("name") == job_name]
    if len(matches) > 1:
        raise GitHubApiError("Ambiguous collector job")
    if not matches:
        if run.get("conclusion") in FAILURES:
            return {"pipeline": "failure"}  # includes jobs0 platform failure
        if run.get("conclusion") == "skipped":
            return {"pipeline": "deferred"}
        raise GitHubApiError("Completed run has no collector evidence")
    job = matches[0]
    steps = job.get("steps", [])
    if any(s.get("name") == "Collection health: diagnostic run" and s.get("conclusion") == "success" for s in steps):
        return {"pipeline": "deferred"}
    health = None
    for step in steps:
        if step.get("conclusion") == "success" and step.get("name", "").startswith(PREFIX):
            key, separator, state = step["name"][len(PREFIX):].partition("=")
            if not separator:
                raise GitHubApiError("Invalid collection health marker")
            if health is None:
                health = {}
            if key in health:
                raise GitHubApiError("Duplicate collection health component")
            health.update(validate_health({key: state}))
    if job.get("status") != "completed":
        if run.get("conclusion") in FAILURES:
            return {"pipeline": "failure"}  # terminal run, queued/no runner
        raise GitHubApiError("Collector job has not settled")
    if job.get("conclusion") == "skipped":
        return {"pipeline": "deferred"}
    failed = job.get("conclusion") in FAILURES or any(s.get("conclusion") in FAILURES for s in steps)
    if failed:
        # A reported source failure is not an unrelated pipeline failure. But
        # failures after the collector (e.g. topic refresh/cleanup) still count.
        if health is None or not any(s in {"failure", "critical"} for s in health.values()):
            return {**(health or {}), "pipeline": "failure"}
        collectors = {"Run persisting AI-practice curation", "Run persisting game-news curation",
                      "Run persisting FMKorea Hot crawl", "Run scheduled DC Hot crawl"}
        if any(s.get("conclusion") in FAILURES and s.get("name") not in collectors for s in steps):
            if health.get("pipeline") != "critical":
                health["pipeline"] = "failure"
        return health
    if health is not None:
        if "pipeline" not in health:
            raise GitHubApiError("Missing collection pipeline marker")
        return health
    if any(s.get("name") == "Failure streak: ignored run" and s.get("conclusion") == "success" for s in steps):
        return {"pipeline": "deferred"}
    if job.get("conclusion") == "success":
        if any(s.get("name") == CONTRACT or s.get("name", "").startswith(PREFIX) for s in steps):
            raise GitHubApiError("New collection contract lost its health evidence")
        # Legacy collector success is authoritative even if the platform later
        # marked the *run* failed. Legacy partial-source detail is unavailable.
        return {"pipeline": "success"}
    raise GitHubApiError("Unknown collector conclusion")


def historical_snapshot(client, run, job_name, next_created_at):
    """One vote per run; later manual retries cannot rewrite an older incident."""
    for attempt in range(run.get("run_attempt", 1), 0, -1):
        rows = client.get_attempt_jobs(run["id"], attempt)
        if attempt == 1:
            metadata = client.get_attempt_run(run["id"], attempt) if run.get("run_attempt", 1) > 1 else run
            return metadata, rows
        starts = [j.get("started_at") for j in rows if j.get("name") == job_name and j.get("started_at")]
        if starts and min(starts) <= next_created_at:
            return client.get_attempt_run(run["id"], attempt), rows
    raise GitHubApiError("No historical collector attempt")


def evaluate(client, current: dict, lane: tuple[str, str, str]) -> Decision:
    decision = Decision(lane[0])
    latest = client.get_run(current["id"])
    if latest.get("run_attempt") != current.get("run_attempt") or latest.get("status") != "completed":
        decision.reason = "superseded_attempt"
        return decision
    current_jobs = client.get_attempt_jobs(current["id"], current["run_attempt"])
    decision.health = classify(latest, current_jobs, lane[2])
    def budget_v2(rows):
        return any(j.get("name") == lane[2] and any(
            s.get("name") == COMMUNITY_BUDGET_CONTRACT and s.get("conclusion") == "success"
            for s in j.get("steps", [])) for j in rows)
    corrected_community = lane[0] in {"fm", "dc"} and budget_v2(current_jobs)
    active = {k for k, v in decision.health.items() if v in {"failure", "critical"}}
    if not active:
        decision.reason = "healthy_or_deferred"
        return decision
    unresolved = set(active)
    counts = {k: 0 for k in active}
    seen = set()
    recovered_after = set()
    next_created_at = current.get("created_at", "")
    for page in client.iter_workflow_run_pages(current["workflow_id"], "main"):
        for run in sorted(page, key=lambda r: r["run_number"], reverse=True):
            if run["id"] in seen:
                continue
            seen.add(run["id"])
            if run["run_number"] > current["run_number"]:
                if current.get("run_attempt", 1) > 1:
                    decision.reason = "superseded_run"
                    return decision  # old manual reruns must not page anew
                if run.get("status") == "completed" and run.get("event") == "workflow_dispatch":
                    newer = classify(run, client.get_attempt_jobs(run["id"], run.get("run_attempt", 1)), lane[2])
                    for key in active:
                        if newer.get(key) == "success":
                            recovered_after.add(key)
                continue
            if run.get("event") != "workflow_dispatch":
                continue
            if len(seen) > MAX_HISTORY:
                raise GitHubApiError("Collection history bound reached without recovery")
            snapshot, prior_jobs = historical_snapshot(client, run, lane[2], next_created_at) if run["id"] != current["id"] else (latest, [])
            if snapshot.get("status") != "completed":
                raise GitHubApiError("Earlier collection attempt is still unsettled")
            health = decision.health if run["id"] == current["id"] else classify(snapshot, prior_jobs, lane[2])
            legacy = (run["id"] != current["id"] and any(j.get("name") == lane[2]
                and j.get("status") == "completed" and j.get("conclusion") != "skipped"
                and not any(s.get("name") == CONTRACT or s.get("name", "").startswith(PREFIX)
                            for s in j.get("steps", [])) for j in prior_jobs))
            for key in list(unresolved):
                if (key.startswith("source:") and corrected_community
                        and run["id"] != current["id"] and key in health
                        and not budget_v2(prior_jobs)):
                    # Old source markers conflate ordinary runtime exhaustion
                    # with real failures. Do not carry that ambiguous count
                    # into the corrected contract or call it source recovery.
                    decision.history_boundary = "community_budget_v2"
                    unresolved.remove(key)
                    continue
                if key.startswith("source:") and legacy and not corrected_community:
                    # Source-level observations start at the v1 contract. Do
                    # not scan thousands of unobservable pre-deployment runs.
                    unresolved.remove(key)
                    continue
                state = health.get(key, "deferred")
                if state in {"failure", "critical"}:
                    counts[key] += 1
                elif state == "success":
                    unresolved.remove(key)
            next_created_at = run.get("created_at", next_created_at)
            if not unresolved:
                break
        if not unresolved:
            break
    if current["id"] not in seen:
        raise GitHubApiError("Current run missing from history")
    decision.streaks = counts
    # No repeat for failures4/5: remind only after three additional failed runs.
    # An integrity failure is actionable immediately, then uses the same cadence.
    decision.alert_components = sorted(k for k, n in counts.items() if
        k not in recovered_after and ((n >= 3 and n % 3 == 0) or (decision.health[k] == "critical" and n == 1)))
    return decision


def was_notified(client, current: dict, observer_run_id: int) -> bool:
    """Native observer retries/events for one raw run must not create a new page.

    A failed Notify step is a receipt of the alert signal, not proof that GitHub
    delivered an email. Read attempts too, since an observer itself can be rerun.
    """
    title = f"Collection alert / {current['id']} /"
    for page in client.iter_workflow_run_pages(OBSERVER, "main"):
        for run in page:
            if not run.get("display_title", "").startswith(title):
                continue
            for attempt in range(run.get("run_attempt", 1), 0, -1):
                for job in client.get_attempt_jobs(run["id"], attempt):
                    if any(s.get("name") == ALERT_STEP and s.get("conclusion") == "failure" for s in job.get("steps", [])):
                        return True
        # Current observer runs are named with upstream ID; GitHub exposes up to
        #1000 recent runs. Older upstream reruns are suppressed by evaluate().
        if len(page) < 100:
            break
        if any(r.get("created_at", "") < current.get("created_at", "") for r in page):
            break
    return False


def run_gate(client, event, repository, observer_run_id):
    current, lane = validate_event(event, repository)
    decision = evaluate(client, current, lane)
    if decision.notify and current.get("conclusion") in FAILURES:
        # GitHub already emitted an upstream failure signal that repository code
        # cannot retract. Keep the incident evidence without a second failed run.
        decision.suppression = "upstream_already_failed"
    if decision.notify and was_notified(client, current, observer_run_id):
        decision.suppression = "already_notified_run"
        decision.reason = "already_notified_run"
    return decision


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
    client = GitHubActionsClient(api_url="https://api.github.com", repository=repository,
                                 token=os.environ["GITHUB_TOKEN"])
    # An unreadable monitor cannot safely pretend that collection recovered.
    # Bounded retry, then a distinct monitor failure (native platform alert).
    for attempt in range(3):
        try:
            decision = run_gate(client, event, repository, int(os.environ["GITHUB_RUN_ID"]))
            break
        except (GitHubApiError, OSError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    data = {**vars(decision), "notify": decision.notify}
    print(json.dumps(data, sort_keys=True))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"notify={str(decision.notify).lower()}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as stream:
        stream.write("## Collection alert decision\n\n```json\n" + json.dumps(data, indent=2) + "\n```\n")
        stream.write("Raw collector steps/results remain authoritative. Deferred does not reset a failure streak.\n")


if __name__ == "__main__":
    main()
