import unittest
from pathlib import Path

from crawler.jobs.check_schema import inspect_schema
from crawler.schema_reads import prime_schema_reads
from tests.test_check_schema import SqliteClient

if (Path(__file__).resolve().parents[1] / "game_news" / "schema_check.py").exists():
    from game_news.schema_check import inspect_game_news_schema
else:
    inspect_game_news_schema = None


class BatchClient:
    def __init__(self, client):
        self.client = client
        self.requests = 0

    def query(self, sql, params=None):
        self.requests += 1
        return self.client.query(sql, params)

    def batch(self, statements):
        self.requests += 1
        return [{"success": True, "results": self.client.query(sql, params)}
                for sql, params in statements]


class SchemaBatchTests(unittest.TestCase):
    def test_batches_preserve_both_reports_and_inspections_remain_fresh(self):
        for inspect in filter(None, (inspect_schema, inspect_game_news_schema)):
            with self.subTest(inspector=inspect.__name__):
                raw = SqliteClient()
                expected = inspect(raw)
                client = BatchClient(raw)
                self.assertEqual(inspect(client), expected)
                self.assertLessEqual(client.requests, 6)
                if inspect is inspect_schema:
                    raw.query("DROP INDEX idx_posts_active_created")
                else:
                    raw.query("UPDATE archives SET is_public = 0 WHERE archive_key = 'game-news'")
                self.assertEqual(inspect(client), inspect(raw))
                self.assertFalse(inspect(client)["valid"])

    def test_incomplete_or_failed_batch_cannot_pass_inspection(self):
        for results in ([], [{"success": False, "results": []}] * 2,
                        [{"success": True}] * 2):
            with self.subTest(results=results):
                client = BatchClient(SqliteClient())
                client.batch = lambda statements: results
                with self.assertRaises(ValueError):
                    prime_schema_reads(client, ["archives"])


if __name__ == "__main__":
    unittest.main()
