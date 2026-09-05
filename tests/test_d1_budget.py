from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from crawler.d1 import (
    D1BudgetExceeded,
    D1Client,
    D1QuotaExceeded,
    D1RunBudget,
    _read_account_daily_usage,
    attach_d1_failure_usage,
    publish_d1_stop_output,
    run_budget_from_env,
)
from crawler.jobs.check_d1_budget import check_daily_budget
from crawler.jobs.run_all_sources import run_all_targets
from crawler.targets import get_target
from tests.test_d1 import FakeResponse


def response(reads=0, writes=0, *, missing_meta=False):
    return {"success": True, "result": [{"success": True, "results": [],
            "meta": {} if missing_meta else {"rows_read": reads, "rows_written": writes}}]}


class D1RunBudgetTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TC_D1_DAILY_GATE_ENABLED": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def client(self, **overrides):
        options = {"rows_read": 20_000, "rows_written": 400}
        options.update(overrides)
        return D1Client("account", "database", "token", run_budget=D1RunBudget(**options))

    def test_profiles_and_invalid_override_cannot_disable_guard(self):
        for profile, writes in (("community-hot", 600), ("fmkorea-hot", 400),
                                ("community-backfill", 800), ("game-news", 500),
                                ("topic", 300)):
            self.assertEqual(run_budget_from_env(profile).rows_written, writes)
        with patch.dict(os.environ, {"TC_D1_COMMUNITY_HOT_MAX_ROWS_WRITTEN": "0"}):
            with self.assertRaises(ValueError):
                run_budget_from_env("community-hot")

    def test_reservation_stops_next_batch_before_request_and_preserves_control_reserve(self):
        client = self.client()
        with patch.object(client, "_request", return_value=response(writes=150)) as send:
            client.execute("UPDATE posts SET upvotes=1", label="post.upsert")
        with patch.object(client, "_request") as send:
            with self.assertRaisesRegex(D1BudgetExceeded, "rows_written"):
                client.execute("UPDATE posts SET upvotes=2", label="post.upsert")
            send.assert_not_called()
        with patch.object(client, "_request", return_value=response(writes=1)) as send:
            client.execute("UPDATE sources SET hot_cursor=1", label="source.state")
            self.assertEqual(send.call_count, 1)
        self.assertEqual(client.usage_summary()["rows_written"], 151)

    def test_read_limit_stops_before_request(self):
        client = self.client(rows_read=3000)
        with patch.object(client, "_request", return_value=response(reads=1100)):
            client.query("SELECT id FROM posts")
        with patch.object(client, "_request") as send:
            with self.assertRaisesRegex(D1BudgetExceeded, "rows_read"):
                client.query("SELECT id FROM posts")
            send.assert_not_called()

    def test_game_projection_batch_fits_run_budget_and_stops_when_headroom_is_low(self):
        client = self.client(rows_written=500)
        statements = [("UPDATE posts SET title='changed'", [])] * 8
        payload = {"success": True, "result": [response(writes=1)["result"][0]] * 8}
        with patch.object(client, "_request", return_value=payload) as send:
            client.batch(statements, label="game-news.projection")
            self.assertEqual(send.call_count, 1)
        with patch.object(client, "_request", return_value=response(writes=370)):
            client.execute("UPDATE candidate SET title='changed'", label="game-news.candidate")
        with patch.object(client, "_request") as send:
            with self.assertRaisesRegex(D1BudgetExceeded, "rows_written"):
                client.batch(statements, label="game-news.projection")
            send.assert_not_called()

    def test_game_source_bootstrap_fits_run_budget(self):
        client = self.client(rows_written=500)
        statements = [("INSERT INTO sources VALUES ('source')", [])] * 8
        payload = {"success": True, "result": [response(writes=1)["result"][0]] * 8}
        with patch.object(client, "_request", return_value=payload) as send:
            client.batch(statements, label="game-news.bootstrap")
        self.assertEqual(send.call_count, 1)

    def test_unknown_usage_forbids_even_control_writes(self):
        client = self.client()
        with patch.object(client, "_request", return_value=response(missing_meta=True)):
            client.query("SELECT id FROM posts")
        with patch.object(client, "_request") as send:
            with self.assertRaisesRegex(D1BudgetExceeded, "usage_unknown"):
                client.execute("UPDATE sources SET hot_cursor=1", label="source.state")
            send.assert_not_called()

    def test_source_budget_does_not_consume_other_source_allocation(self):
        client = self.client(rows_written=600)
        with client.source_budget(200):
            with patch.object(client, "_request", return_value=response(writes=10)):
                client.execute("UPDATE posts SET upvotes=1", label="post.upsert")
            with self.assertRaisesRegex(D1BudgetExceeded, "source_rows_written"):
                client.execute("UPDATE posts SET upvotes=2", label="post.upsert")
        with client.source_budget(200):
            with patch.object(client, "_request", return_value=response(writes=10)) as send:
                client.execute("UPDATE posts SET upvotes=3", label="post.upsert")
                self.assertEqual(send.call_count, 1)

    def test_quota_error_latches_across_sources_and_no_further_requests(self):
        client = self.client()
        calls = []

        def runner(target, mode, run_client):
            calls.append(target.key)
            # CrawlCycle catches RuntimeError and returns failed; the latch must survive.
            try:
                run_client.query("SELECT 1")
            except RuntimeError:
                return {"status": "failed"}

        with patch.object(client, "_request", side_effect=RuntimeError(
            "Your account has exceeded D1's free tier daily row write limit"
        )) as send:
            result = run_all_targets(mode="hot", client=client, runner=runner,
                                     targets=(get_target("dcinside-singularity"),
                                              get_target("dcinside-ai-utilize")))
        self.assertEqual(len(calls), 1)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(result["results"][1]["stop_reason"], "daily_quota")
        self.assertFalse(result["results"][1]["source_requested"])
        self.assertEqual(result["d1_usage"]["failed_request_count"], 1)

    def test_every_source_gets_first_budget_allocation_across_utc_slots(self):
        targets = tuple(get_target(key) for key in (
            "dcinside-singularity", "dcinside-ai-utilize", "dcinside-zeus-pride"))
        for mode, minutes in (("hot", 30), ("backfill", 360)):
            first_sources = []
            for slot in range(3):
                client = self.client(rows_written=600)
                calls = []

                def runner(target, mode, run_client):
                    calls.append(target.key)
                    for _ in range(3):
                        # Two writes from the first source consume 300 actual
                        # rows; a second source can use 60, and a third is gated.
                        writes = 150 if len(calls) == 1 else 60
                        with patch.object(client, "_request", return_value=response(writes=writes)):
                            run_client.execute("UPDATE posts SET upvotes=1", label="post.upsert")
                    return {"status": "completed"}

                at = datetime(2026, 9, 5, tzinfo=timezone.utc) + timedelta(minutes=slot * minutes)
                result = run_all_targets(mode=mode, client=client, targets=targets,
                                         scheduled_at=at, runner=runner)
                first_sources.append(calls[0])
                self.assertEqual(result["planned_source_order"][0], calls[0])
                self.assertEqual(result["source_rotation_period_seconds"], minutes * 60)
                self.assertEqual(result["planned_source_order"],
                                 [item["target"] for item in result["results"]])
            self.assertEqual(set(first_sources), {target.key for target in targets})

    def test_snapshot_uses_reserved_reads_after_optional_analysis_read_stop(self):
        client = D1Client("account", "db", "token", run_budget=run_budget_from_env("topic"))
        with patch.object(client, "_request", return_value=response(reads=15_000)):
            client.query("SELECT id FROM posts", label="topic")
        with self.assertRaisesRegex(D1BudgetExceeded, "rows_read"):
            client.query("SELECT id FROM posts", label="topic")
        client.check_budget(reserved=True)
        with patch.object(client, "_request", return_value=response(reads=100)) as send:
            client.query("SELECT id FROM posts", label="topic.snapshot")
        self.assertEqual(send.call_count, 1)
        client._quota_exhausted = True
        with patch.object(client, "_request") as send:
            with self.assertRaises(D1QuotaExceeded):
                client.check_budget(reserved=True)
            with self.assertRaises(D1QuotaExceeded):
                client.query("SELECT id FROM posts", label="topic.snapshot")
            send.assert_not_called()


class D1DailyGateTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"TC_D1_DAILY_GATE_ENABLED": "1",
            "TC_CF_ACCOUNT_ID": "account", "TC_CF_D1_ANALYTICS_TOKEN": "secret-token"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_gate_includes_whole_game_job_bootstrap_and_latency_reserve(self):
        with patch("crawler.jobs.check_d1_budget._read_account_daily_usage",
                   return_value={"utc_day": "2026-09-05", "rows_read": 1, "rows_written": 69_073}):
            result = check_daily_budget(["game-news", "topic"])
        self.assertEqual(result["next_run_rows_written"], 928)
        self.assertEqual(result["stop_reason"], "account_rows_written")
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["hard_quota_guarantee"])

    def test_missing_or_unavailable_analytics_never_admits_work(self):
        with patch.dict(os.environ, {"TC_CF_D1_ANALYTICS_TOKEN": ""}):
            self.assertEqual(check_daily_budget(["community-hot"])["status"], "blocked")
            client = D1Client("account", "db", "token", run_budget=run_budget_from_env("community-hot"))
            with self.assertRaisesRegex(D1BudgetExceeded, "account_analytics_unavailable"):
                client.check_budget()
        with patch("crawler.jobs.check_d1_budget._read_account_daily_usage", side_effect=RuntimeError("secret")):
            result = check_daily_budget(["community-hot"])
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("secret", json.dumps(result))

    def test_client_refresh_failure_revokes_stale_success(self):
        client = D1Client("account", "db", "token", run_budget=run_budget_from_env("community-hot"))
        from datetime import datetime, timezone
        usage = {"utc_day": datetime.now(timezone.utc).date().isoformat(), "rows_read": 1, "rows_written": 1}
        with patch("crawler.d1._read_account_daily_usage", side_effect=[usage, RuntimeError("unavailable")]), \
             patch("crawler.d1.time.monotonic", side_effect=[1, 62, 62]):
            client.check_budget()
            with self.assertRaisesRegex(D1BudgetExceeded, "account_analytics_unavailable"):
                client.check_budget()
        self.assertIsNone(client.budget_status()["account_usage"])

    def test_analytics_sums_account_without_database_filter_and_checks_meta(self):
        payload = {"data": {"viewer": {"accounts": [{"d1AnalyticsAdaptiveGroups": [
            {"sum": {"rowsRead": 100, "rowsWritten": 5}}]}]}}}
        with patch("crawler.d1.request.urlopen", return_value=FakeResponse(payload)) as send:
            result = _read_account_daily_usage("account", "secret-token", "2026-09-05")
        query = json.loads(send.call_args.args[0].data)["query"]
        self.assertNotIn("database", query.lower())
        self.assertEqual(result["rows_written"], 5)
        payload["data"]["viewer"]["accounts"][0]["d1AnalyticsAdaptiveGroups"][0]["sum"]["rowsRead"] = None
        with patch("crawler.d1.request.urlopen", return_value=FakeResponse(payload)):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                _read_account_daily_usage("account", "token", "2026-09-05")


class PipelineBudgetTests(unittest.TestCase):
    @unittest.skipUnless((Path(__file__).resolve().parents[1] / "game_news").is_dir(),
                         "Game-news pipeline is private and omitted from the public mirror")
    def test_game_models_do_not_run_when_minimum_next_persistence_cannot_fit(self):
        from game_news.runner import GameNewsPipeline
        for phase in ("collection", "filter"):
            client = D1Client("account", "db", "token", run_budget=run_budget_from_env("game-news"))
            with patch.object(client, "_request", return_value=response(writes=420)):
                client.execute("INSERT INTO candidates VALUES (1)", label="game-news.candidate")
            client.check_budget()  # Zero-estimate checkpoint alone would allow it.
            pipeline = object.__new__(GameNewsPipeline)
            pipeline.persistence = SimpleNamespace(check_budget=client.check_budget)
            pipeline.executor = Mock()
            with self.assertRaisesRegex(D1BudgetExceeded, "rows_written"):
                if phase == "collection":
                    pipeline._run_collection_attempt("prompt", fallback=False)
                else:
                    pipeline._run_filter_attempt("prompt", {}, "", {}, {}, fallback=False)
            pipeline.executor.run_phase.assert_not_called()

    @unittest.skipUnless((Path(__file__).resolve().parents[1] / "community_topics").is_dir(),
                         "Topic pipeline is private and omitted from the public mirror")
    def test_topic_snapshots_continue_after_analysis_budget_stop_without_model_call(self):
        from community_topics.runner import CommunityTopicPipeline, PipelinePaths, TopicPipelineConfig
        for written in (109, 230):
            with self.subTest(written=written), tempfile.TemporaryDirectory() as root:
                client = D1Client("account", "db", "token", run_budget=run_budget_from_env("topic"))
                with patch.object(client, "_request", return_value=response(writes=written)):
                    client.execute("INSERT INTO analyses VALUES (1)", label="topic")
                archives = [{"archive_key": f"archive-{index}"} for index in range(4)]

                def load_pending(*args, **kwargs):
                    if written == 109:
                        # A 20-statement analysis batch cannot fit, but a
                        # single-row latest snapshot must still be publishable.
                        client.batch([("UPDATE analyses SET version=1", [])] * 20, label="topic")
                    return [SimpleNamespace(post_id=1)]

                def snapshot(**kwargs):
                    with patch.object(client, "_request", return_value=response(writes=1)):
                        client.execute("INSERT INTO community_topic_latest VALUES (1)", label="topic.snapshot")
                    return {"topic_count": 0}

                storage = SimpleNamespace(client=client,
                    list_public_community_archives=lambda: archives,
                    load_pending_posts=load_pending, load_active_topics=lambda *args, **kwargs: [],
                    create_snapshot=snapshot)
                path = Path(root)
                (path / "instruction.md").write_text("Topic instruction", encoding="utf-8")
                executor = Mock()
                pipeline = CommunityTopicPipeline(run_id="test", run_root=path,
                    storage=storage, executor=executor,
                    paths=PipelinePaths(path, path / "unused.json", path / "instruction.md"),
                    config=TopicPipelineConfig(), persist=True, now=datetime.now(timezone.utc))
                result = pipeline.run()
                self.assertEqual(result["status"], "partial")
                self.assertEqual(result["analysis_stop_reason"], "rows_written")
                self.assertFalse(result["persistence_counts_complete"])
                self.assertEqual(len(result["snapshots"]), 4)
                self.assertEqual(client.usage_summary()["rows_written"], written + 4)
                executor.run_phase.assert_not_called()

    def test_finalizer_partial_write_budget_stop_cannot_advance_coverage(self):
        from crawler.jobs.run_cycle import CrawlCycle
        from crawler.jobs.scan_new_posts import upsert_source
        from tests.test_run_cycle import FIXED_NOW, SqliteClient, config, post, runtime

        class StopSecondBatch(SqliteClient):
            def __init__(self):
                super().__init__()
                self.post_batches = 0

            def query(self, sql, params=None):
                if "INSERT INTO posts" in sql:
                    self.post_batches += 1
                    if self.post_batches == 2:
                        raise D1BudgetExceeded("rows_written")
                return super().query(sql, params)

        client = StopSecondBatch()
        target = get_target("dcinside-singularity")
        upsert_source(client, target, FIXED_NOW.isoformat())
        settings = config()
        cycle = CrawlCycle(target=target, config=settings, runtime=runtime(settings),
                           client=client, cycle_started_at=FIXED_NOW, mode="backfill")
        posts = [post(index, "2026-07-14T00:00:00Z") for index in range(100, 88, -1)]
        with self.assertRaises(D1BudgetExceeded):
            cycle._commit_finalized_page(posts)
        self.assertEqual(len(client.query("SELECT id FROM posts")), 6)
        self.assertEqual(client.query("SELECT * FROM coverage_intervals"), [])
        self.assertEqual(cycle.coverage, [])

    @unittest.skipUnless((Path(__file__).resolve().parents[1] / "game_news").is_dir(),
                         "Game-news pipeline is private and omitted from the public mirror")
    def test_game_pipeline_stops_before_model_or_browser_setup(self):
        from game_news.runner import GameNewsPipeline
        pipeline = object.__new__(GameNewsPipeline)
        check = Mock(side_effect=D1QuotaExceeded("quota"))
        pipeline.persistence = SimpleNamespace(check_budget=check)
        pipeline.executor = Mock()
        with self.assertRaises(D1QuotaExceeded):
            pipeline.run()
        check.assert_called_once()
        pipeline.executor.run_phase.assert_not_called()

    @unittest.skipUnless(all((Path(__file__).resolve().parents[1] / name).is_dir()
                             for name in ("game_news", "community_topics")),
                         "Private pipelines are omitted from the public mirror")
    def test_failure_counters_reach_stdout_for_both_pipelines(self):
        for module_name in ("game_news.runner", "community_topics.runner"):
            with self.subTest(module=module_name):
                import importlib
                module = importlib.import_module(module_name)
                exc = D1BudgetExceeded("rows_written")
                client = D1Client("account", "db", "secret")
                attach_d1_failure_usage(exc, client)
                output = io.StringIO()
                with patch.object(module, "run_pipeline", side_effect=exc), \
                     redirect_stdout(output), self.assertRaises(SystemExit) as stopped:
                    module.main(["--run-root", "unused"])
                self.assertEqual(stopped.exception.code, 1)
                failure = json.loads(output.getvalue())
                self.assertEqual(failure["stop_reason"], "rows_written")
                self.assertIn("d1_usage", failure)
                self.assertNotIn("secret", output.getvalue())

    def test_game_quota_stop_is_visible_to_following_topic_step(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "outputs"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
                publish_d1_stop_output(D1QuotaExceeded("quota"))
            self.assertEqual(output.read_text(encoding="utf-8"), "d1_stop=true\n")


if __name__ == "__main__":
    unittest.main()
