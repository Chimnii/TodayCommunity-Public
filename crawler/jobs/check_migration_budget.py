"""Read-only apply-readiness gate for dropping the two source metric indexes."""
from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Mapping

from crawler.config import get_required_env
from crawler.d1 import D1Client, _read_account_daily_usage, d1_usage_summary


INDEX_COLUMNS = {
    "idx_posts_source_upvotes": "upvotes",
    "idx_posts_source_comments": "comments",
}
WORKFLOWS = (
    ("Chimnii/TodayCommunity-Public", "scan-dcinside.yml"),
    ("Chimnii/TodayCommunity-Public", "scan-dcinside-backfill.yml"),
    ("Chimnii/TodayCommunity", "scan-fmkorea.yml"),
    ("Chimnii/TodayCommunity", "scan-game-news.yml"),
)
ACTIVE_RUN_STATES = ("queued", "in_progress", "waiting", "pending", "requested")


def _gh_json(endpoint: str) -> dict:
    completed = subprocess.run(["gh", "api", endpoint], capture_output=True,
                               text=True, encoding="utf-8", check=True, timeout=30)
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict):
        raise ValueError("Invalid GitHub inventory")
    return payload


def _workflow_state(identity: tuple[str, str]) -> dict:
    repo, workflow = identity
    endpoint = f"repos/{repo}/actions/workflows/{workflow}"
    state = _gh_json(endpoint).get("state")
    active = {}
    stale = []
    unverified = []
    dormant = []
    verified_runs = {}
    for status in ACTIVE_RUN_STATES:
        listing = _gh_json(endpoint + f"/runs?status={status}&per_page=100")
        count = listing.get("total_count")
        if type(count) is not int or count < 0:
            raise ValueError("Invalid GitHub run count")
        active[status] = 0
        if not count:
            continue
        runs = listing.get("workflow_runs")
        if (count > 100 or not isinstance(runs, list) or len(runs) != count
                or len(verified_runs) + count > 100):
            active[status] = count
            unverified.append({"listed_status": status, "count": count})
            continue
        identifiers = [run.get("id") for run in runs if isinstance(run, dict)]
        if (len(identifiers) != count or len(set(identifiers)) != count
                or any(type(run_id) is not int or run_id <= 0 for run_id in identifiers)):
            raise ValueError("Invalid GitHub run identity")
        for run_id in identifiers:
            if run_id not in verified_runs:
                verified_runs[run_id] = _gh_json(f"repos/{repo}/actions/runs/{run_id}")
            actual = verified_runs[run_id]
            if actual.get("id") != run_id:
                raise ValueError("GitHub run identity changed")
            if actual.get("status") == "completed":
                # GitHub's status-filtered index can retain a queued entry
                # after its authoritative run record is already completed.
                stale.append({"run_id": run_id, "listed_status": status,
                              "actual_status": "completed", "conclusion": actual.get("conclusion")})
            elif status == "queued" and actual.get("status") == "queued":
                paused = _dormant_queued_run(repo, run_id)
                if paused is not None:
                    dormant.append(paused)
                else:
                    active[status] += 1
            else:
                active[status] += 1
    return {"repository": repo, "workflow": workflow, "state": state,
            "active_runs": active, "stale_listed_runs": stale, "unverified_active_runs": unverified,
            "dormant_self_hosted_queue": dormant}


def _dormant_queued_run(repo: str, run_id: int) -> dict | None:
    """A disabled workflow's queued jobs cannot start while all matching hosts are offline."""
    jobs_payload = _gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page=100")
    jobs = jobs_payload.get("jobs")
    count = jobs_payload.get("total_count")
    if (type(count) is not int or not 1 <= count <= 100
            or not isinstance(jobs, list) or len(jobs) != count):
        return None
    requirements = []
    for job in jobs:
        if (not isinstance(job, dict) or job.get("status") != "queued"
                or "runner_id" not in job or job["runner_id"] is not None):
            return None
        labels = job.get("labels")
        if (not isinstance(labels, list) or not labels
                or any(not isinstance(label, str) or not label.strip() for label in labels)):
            return None
        normalized = {label.strip().lower() for label in labels}
        if "self-hosted" not in normalized:
            return None
        requirements.append(normalized)
    inventory = _gh_json(f"repos/{repo}/actions/runners?per_page=100")
    runners = inventory.get("runners")
    total = inventory.get("total_count")
    if (type(total) is not int or not 1 <= total <= 100
            or not isinstance(runners, list) or len(runners) != total):
        return None
    normalized_runners = []
    for runner in runners:
        if not isinstance(runner, dict) or type(runner.get("id")) is not int:
            return None
        labels = runner.get("labels")
        if (not isinstance(labels, list) or any(not isinstance(label, dict)
                or not isinstance(label.get("name"), str) for label in labels)):
            return None
        normalized_runners.append((runner, {label["name"].strip().lower() for label in labels}))
    matching_ids = set()
    for required in requirements:
        matching = [runner for runner, labels in normalized_runners if required <= labels]
        if not matching or any(runner.get("status") != "offline" for runner in matching):
            return None
        matching_ids.update(runner["id"] for runner in matching)
    return {"run_id": run_id, "queued_job_count": count,
            "matching_offline_runner_ids": sorted(matching_ids),
            "required_labels": [sorted(labels) for labels in requirements],
            "condition": "keep_all_matching_runner_services_stopped_until_migration_and_validation_finish"}


def load_workflow_inventory() -> list[dict]:
    with ThreadPoolExecutor(max_workers=4) as executor:
        return list(executor.map(_workflow_state, WORKFLOWS))


def _workflows_paused(inventory: list[dict]) -> bool:
    identities = [(item.get("repository"), item.get("workflow")) for item in inventory]
    if len(identities) != len(WORKFLOWS) or set(identities) != set(WORKFLOWS):
        return False
    return all(item.get("state") == "disabled_manually"
               and isinstance(item.get("active_runs"), dict)
               and all(type(item["active_runs"].get(state)) is int
                       and item["active_runs"][state] == 0 for state in ACTIVE_RUN_STATES)
               for item in inventory)


def _index_definitions(client: object) -> tuple[dict, bool]:
    indexes = {row["name"]: row for row in client.query("PRAGMA index_list(posts)")}
    definitions = {}
    valid = True
    for name, metric in INDEX_COLUMNS.items():
        # Names are fixed code constants, never operator-controlled SQL fragments.
        row = indexes.get(name)
        keys = [item for item in client.query(f"PRAGMA index_xinfo({name})")
                if item.get("key") == 1]
        keys.sort(key=lambda item: item.get("seqno", -1))
        columns = [(item.get("name"), item.get("desc"), item.get("coll")) for item in keys]
        matches = bool(row and row.get("unique") == 0 and row.get("partial") == 0
                       and row.get("origin") == "c"
                       and columns == [("source_key", 0, "BINARY"), (metric, 1, "BINARY")])
        definitions[name] = {"exists": row is not None, "keys": keys, "matches_expected": matches}
        valid = valid and matches
    return definitions, valid


def migration_preflight(
    client: object, *, read_daily_usage: Callable[[str], Mapping[str, object]],
    workflow_inventory: Callable[[], list[dict]] = load_workflow_inventory,
) -> dict:
    report = {"status": "blocked", "migration": "016_drop_unused_source_metric_indexes",
              "captured_at": datetime.now(timezone.utc).isoformat(),
              "indexes": list(INDEX_COLUMNS), "hard_quota_guarantee": False,
              "scope": "one_off_maintenance_with_rollback_reserve",
              "physical_daily_limits": {"rows_read": 5_000_000, "rows_written": 100_000}}
    blockers = []
    try:
        inventory = workflow_inventory()
        report["workflows"] = inventory
        if not _workflows_paused(inventory):
            blockers.append("collection_workflows_not_paused_and_drained")
    except Exception:
        blockers.append("workflow_inventory_unavailable")
    try:
        definitions, valid = _index_definitions(client)
        report["index_definitions"] = definitions
        if not valid:
            blockers.append("unexpected_index_definitions")
        rows = client.query("SELECT COUNT(*) AS post_count FROM posts")
        count = rows[0]["post_count"] if len(rows) == 1 else None
        if type(count) is not int or count < 0:
            raise ValueError("Invalid full posts count")
        report["full_posts_count"] = count
        query_usage = d1_usage_summary(client)
        report["preflight_d1_usage"] = query_usage
        observed_reads = int(query_usage["rows_read"]) if query_usage else count + 100
        if query_usage and (query_usage["incomplete_meta_count"] or query_usage["failed_request_count"]):
            blockers.append("preflight_usage_unknown")
        utc_day = datetime.now(timezone.utc).date().isoformat()
        usage = dict(read_daily_usage(utc_day))
        if usage.get("utc_day") != utc_day or any(
            type(usage.get(key)) is not int or usage[key] < 0
            for key in ("rows_read", "rows_written")
        ):
            raise ValueError("Invalid current UTC account usage")
        report["account_usage"] = usage
        writes = {"rollback_two_indexes": 2 * (count + 1) + 1000,
                  "ddl": 128, "verification": 100, "concurrent_and_resume": 3000,
                  "analytics_latency": 10_000}
        reads = {"rollback_two_indexes": 4 * count + 4, "verification": 100,
                 "preflight_not_yet_in_analytics": observed_reads,
                 "analytics_latency": 100_000}
        report["estimated_reservations"] = {"rows_written": writes, "rows_read": reads}
        totals = {"rows_written": usage["rows_written"] + sum(writes.values()),
                  "rows_read": usage["rows_read"] + sum(reads.values())}
        report["projected_daily_totals_with_rollback"] = totals
        for metric, limit in report["physical_daily_limits"].items():
            if totals[metric] > limit:
                blockers.append("insufficient_" + metric + "_rollback_headroom")
    except Exception:
        blockers.append("d1_or_account_usage_unavailable")
    report["blockers"] = blockers
    if not blockers:
        report["status"] = "ready"
    return report


def main() -> None:
    try:
        account = get_required_env("TC_CF_ACCOUNT_ID")
        analytics_token = get_required_env("TC_CF_D1_ANALYTICS_TOKEN")
        client = D1Client(account, get_required_env("TC_CF_DATABASE_ID"),
                          get_required_env("TC_CF_API_TOKEN"))
        report = migration_preflight(client, read_daily_usage=lambda day:
            _read_account_daily_usage(account, analytics_token, day))
    except Exception:
        report = {"status": "blocked", "blockers": ["required_credentials_unavailable"],
                  "hard_quota_guarantee": False}
    print(json.dumps({"d1_migration_gate": report}, ensure_ascii=False, indent=2))
    if report["status"] != "ready":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
