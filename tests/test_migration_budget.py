from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from crawler.jobs.check_migration_budget import (
    ACTIVE_RUN_STATES, INDEX_COLUMNS, WORKFLOWS, migration_preflight,
    _workflow_state,
    _dormant_queued_run,
)


class ReadOnlyClient:
    def __init__(self, count=20_000):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("CREATE TABLE posts(source_key TEXT, upvotes INTEGER, comments INTEGER)")
        for name, metric in INDEX_COLUMNS.items():
            self.db.execute(f"CREATE INDEX {name} ON posts(source_key, {metric} DESC)")
        self.db.executemany("INSERT INTO posts VALUES ('source', 1, 1)", [()] * count)
        self.db.execute("PRAGMA query_only=ON")

    def query(self, sql):
        return [dict(row) for row in self.db.execute(sql)]


def paused_inventory():
    return [{"repository": repo, "workflow": workflow, "state": "disabled_manually",
             "active_runs": {status: 0 for status in ACTIVE_RUN_STATES}}
            for repo, workflow in WORKFLOWS]


def usage(writes=40_000, reads=500_000):
    return lambda day: {"utc_day": day, "rows_written": writes, "rows_read": reads}


class MigrationBudgetTests(unittest.TestCase):
    def setUp(self):
        self.client = ReadOnlyClient()

    def check(self, **overrides):
        args = {"read_daily_usage": usage(), "workflow_inventory": paused_inventory}
        args.update(overrides)
        return migration_preflight(self.client, **args)

    def test_read_only_gate_reserves_complete_rollback_above_normal_operating_ceiling(self):
        result = self.check()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["full_posts_count"], 20_000)
        self.assertEqual(result["estimated_reservations"]["rows_written"]["rollback_two_indexes"], 41_002)
        self.assertEqual(result["projected_daily_totals_with_rollback"]["rows_written"], 94_230)
        self.assertFalse(result["hard_quota_guarantee"])
        self.assertEqual(len(self.client.query("PRAGMA index_list(posts)")), 2)

    def test_exact_physical_limit_boundary_and_one_extra_write(self):
        self.assertEqual(self.check(read_daily_usage=usage(writes=45_770))["status"], "ready")
        denied = self.check(read_daily_usage=usage(writes=45_771))
        self.assertIn("insufficient_rows_written_rollback_headroom", denied["blockers"])

    def test_read_rollback_budget_blocks_even_if_writes_fit(self):
        result = self.check(read_daily_usage=usage(reads=4_950_000))
        self.assertIn("insufficient_rows_read_rollback_headroom", result["blockers"])

    def test_all_four_workflows_must_be_paused_and_drained(self):
        active = paused_inventory()
        active[0]["state"] = "active"
        for inventory in (paused_inventory()[:-1], active):
            result = self.check(workflow_inventory=lambda: inventory)
            self.assertIn("collection_workflows_not_paused_and_drained", result["blockers"])
        inventory = paused_inventory()
        inventory[0]["active_runs"]["in_progress"] = 1
        self.assertEqual(self.check(workflow_inventory=lambda: inventory)["status"], "blocked")

    def test_unavailable_analytics_and_stale_day_fail_closed(self):
        def unavailable(day):
            raise RuntimeError("credential must not appear")
        result = self.check(read_daily_usage=unavailable)
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("credential must not appear", str(result))
        result = self.check(read_daily_usage=lambda day: {"utc_day": "2000-01-01", "rows_read": 0, "rows_written": 0})
        self.assertEqual(result["status"], "blocked")

    def test_changed_sort_or_missing_index_blocks_drop_readiness(self):
        self.client.db.execute("PRAGMA query_only=OFF")
        self.client.db.execute("DROP INDEX idx_posts_source_upvotes")
        self.client.db.execute("CREATE INDEX idx_posts_source_upvotes ON posts(source_key, upvotes ASC)")
        self.client.db.execute("PRAGMA query_only=ON")
        self.assertIn("unexpected_index_definitions", self.check()["blockers"])
        self.client.db.execute("PRAGMA query_only=OFF")
        self.client.db.execute("DROP INDEX idx_posts_source_comments")
        self.client.db.execute("PRAGMA query_only=ON")
        self.assertIn("unexpected_index_definitions", self.check()["blockers"])

    def test_stale_queued_index_is_ignored_only_after_authoritative_completion(self):
        def github(endpoint):
            if endpoint.endswith("/actions/runs/123"):
                return {"id": 123, "status": "completed", "conclusion": "cancelled"}
            if "/runs?status=queued" in endpoint:
                return {"total_count": 1, "workflow_runs": [{"id": 123}]}
            if "/runs?" in endpoint:
                return {"total_count": 0, "workflow_runs": []}
            return {"state": "disabled_manually"}
        with patch("crawler.jobs.check_migration_budget._gh_json", side_effect=github):
            entry = _workflow_state(WORKFLOWS[0])
        self.assertEqual(sum(entry["active_runs"].values()), 0)
        self.assertEqual(entry["stale_listed_runs"][0]["run_id"], 123)
        inventory = paused_inventory()
        inventory[0] = entry
        self.assertEqual(self.check(workflow_inventory=lambda: inventory)["status"], "ready")

    def test_large_or_authoritatively_active_inventory_remains_blocked(self):
        for count in (1, 101):
            lookups = []
            def github(endpoint):
                if endpoint.endswith("/actions/runs/123"):
                    lookups.append(endpoint)
                    return {"id": 123, "status": "in_progress"}
                if "/runs?status=queued" in endpoint:
                    return {"total_count": count, "workflow_runs": [{"id": 123}]}
                if "/runs?" in endpoint:
                    return {"total_count": 0, "workflow_runs": []}
                return {"state": "disabled_manually"}
            with patch("crawler.jobs.check_migration_budget._gh_json", side_effect=github):
                entry = _workflow_state(WORKFLOWS[0])
            self.assertEqual(entry["active_runs"]["queued"], count)
            self.assertEqual(len(lookups), 1 if count == 1 else 0)
            inventory = paused_inventory()
            inventory[0] = entry
            self.assertEqual(self.check(workflow_inventory=lambda: inventory)["status"], "blocked")

    def test_queued_self_hosted_run_is_dormant_only_with_complete_matching_offline_inventory(self):
        labels = ["self-hosted", "Windows", "X64", "todaycommunity-game-news"]
        job = {"status": "queued", "runner_id": None, "labels": labels}
        runner = {"id": 22, "status": "offline", "labels": [{"name": label} for label in labels]}
        inventory = {"total_count": 1, "runners": [runner]}

        def github(endpoint):
            if "/jobs?" in endpoint:
                return {"total_count": 1, "jobs": [job]}
            if "/actions/runners?" in endpoint:
                return inventory
            raise AssertionError(endpoint)

        with patch("crawler.jobs.check_migration_budget._gh_json", side_effect=github):
            dormant = _dormant_queued_run("repo", 123)
            self.assertEqual(dormant["matching_offline_runner_ids"], [22])
            runner["status"] = "online"
            self.assertIsNone(_dormant_queued_run("repo", 123))
            runner["status"] = "offline"
            job["status"] = "in_progress"
            self.assertIsNone(_dormant_queued_run("repo", 123))
            job["status"] = "queued"
            job["runner_id"] = 22
            self.assertIsNone(_dormant_queued_run("repo", 123))
            job["runner_id"] = None
            job["labels"] = ["ubuntu-latest"]
            self.assertIsNone(_dormant_queued_run("repo", 123))
            job["labels"] = labels
            runner["labels"] = [{"name": "different-label"}]
            self.assertIsNone(_dormant_queued_run("repo", 123))
            runner["labels"] = [{"name": label} for label in labels]
            inventory["total_count"] = 2
            self.assertIsNone(_dormant_queued_run("repo", 123))
            inventory["runners"] = [runner, {**runner, "id": 23, "status": "online"}]
            self.assertIsNone(_dormant_queued_run("repo", 123))
            inventory.update(total_count=0, runners=[])
            self.assertIsNone(_dormant_queued_run("repo", 123))

    def test_dormant_queue_never_overrides_enabled_workflow(self):
        def github(endpoint):
            if endpoint.endswith("/actions/runs/123"):
                return {"id": 123, "status": "queued"}
            if "/runs?status=queued" in endpoint:
                return {"total_count": 1, "workflow_runs": [{"id": 123}]}
            if "/runs?" in endpoint:
                return {"total_count": 0, "workflow_runs": []}
            return {"state": "active"}
        with patch("crawler.jobs.check_migration_budget._gh_json", side_effect=github), \
             patch("crawler.jobs.check_migration_budget._dormant_queued_run", return_value={"run_id": 123}):
            entry = _workflow_state(WORKFLOWS[0])
        self.assertEqual(entry["active_runs"]["queued"], 0)
        inventory = paused_inventory()
        inventory[0] = entry
        self.assertEqual(self.check(workflow_inventory=lambda: inventory)["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
