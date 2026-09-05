from __future__ import annotations

import sqlite3
import unittest
from dataclasses import replace

from crawler.jobs.scan_new_posts import mark_posts_deleted, upsert_posts, upsert_source
from crawler.targets import get_target
from tests.test_scan_new_posts import SqliteClient, sample_post


class PostEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.client = SqliteClient()
        self.addCleanup(self.client.connection.close)
        self.target = get_target("dcinside-singularity")
        self.first = "2026-09-06T00:00:00Z"
        self.later = "2026-09-06T00:15:00Z"
        upsert_source(self.client, self.target, self.first)

    def assert_counts(self, count, subject):
        archive = self.client.query("SELECT active_post_count FROM archive_stats WHERE archive_key=?", [self.target.archive_key])[0]
        self.assertEqual(archive["active_post_count"], count)
        subjects = self.client.query("SELECT subject, active_post_count FROM archive_subject_stats WHERE archive_key=?", [self.target.archive_key])
        self.assertEqual(subjects, [{"subject": subject, "active_post_count": count}])

    def test_observation_changes_preserve_index_columns_and_sequence(self):
        upsert_posts(self.client, self.target, [sample_post(1)], self.first)
        sequence = self.client.query("SELECT seq FROM sqlite_sequence WHERE name='posts'")
        self.client.connection.executescript("""
            CREATE TABLE audit_columns(kind TEXT);
            CREATE TRIGGER audit_indexed AFTER UPDATE OF archive_key, canonical_post_key,
                created_at, status, upvotes, comments ON posts
            BEGIN INSERT INTO audit_columns VALUES ('indexed'); END;
        """)
        changes = {"title": "latest title", "post_url": "https://example.com/1?page=5",
                   "created_at_raw": "09:00", "qualifies_by": "score"}
        upsert_posts(self.client, self.target, [{**sample_post(1), **changes}], self.later)
        self.assertEqual(self.client.query("SELECT * FROM audit_columns"), [])
        self.assertEqual(self.client.query("SELECT seq FROM sqlite_sequence WHERE name='posts'"), sequence)
        row = self.client.query("SELECT * FROM posts")[0]
        for column, value in changes.items():
            self.assertEqual(row[column], value)
        self.assertEqual(row["last_seen_at"], self.later)
        self.assert_counts(1, "일반")

    def test_insert_between_planning_and_batch_is_counted_once(self):
        original_batch = self.client.batch
        fired = False

        def batch(statements):
            nonlocal fired
            statements = list(statements)
            if not fired and any("INSERT INTO posts" in sql for sql, _ in statements):
                fired = True
                upsert_posts(self.client, self.target, [{**sample_post(1), "subject": "first"}], self.first)
            return original_batch(statements)

        self.client.batch = batch
        upsert_posts(self.client, self.target, [{**sample_post(1), "subject": "later", "upvotes": 12}], self.later)
        self.assert_counts(1, "first")
        self.assertEqual(self.client.query("SELECT upvotes FROM posts")[0]["upvotes"], 12)

    def test_status_change_before_batch_uses_actual_transition(self):
        upsert_posts(self.client, self.target, [sample_post(1)], self.first)
        original_batch = self.client.batch
        fired = False

        def batch(statements):
            nonlocal fired
            statements = list(statements)
            if not fired and any("INSERT INTO posts" in sql for sql, _ in statements):
                fired = True
                mark_posts_deleted(self.client, self.target, ["1"])
            return original_batch(statements)

        self.client.batch = batch
        upsert_posts(self.client, self.target, [sample_post(1)], self.later)
        self.assert_counts(1, "일반")
        self.assertEqual(self.client.query("SELECT status FROM posts")[0]["status"], "active")

    def test_failed_post_write_rolls_back_statistics_and_retry_counts_once(self):
        self.client.connection.executescript("""
            CREATE TRIGGER reject_post BEFORE INSERT ON posts
            BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END;
        """)
        with self.assertRaises(sqlite3.IntegrityError):
            upsert_posts(self.client, self.target, [sample_post(1)], self.first)
        self.assertEqual(self.client.query("SELECT active_post_count FROM archive_stats WHERE archive_key=?", [self.target.archive_key])[0]["active_post_count"], 0)
        self.assertEqual(self.client.query("SELECT * FROM archive_subject_stats"), [])
        self.client.connection.executescript("DROP TRIGGER reject_post;")
        upsert_posts(self.client, self.target, [sample_post(1)], self.first)
        self.assert_counts(1, "일반")

    def test_duplicate_input_keeps_first_subject_latest_fields_and_one_count(self):
        upsert_posts(self.client, self.target, [
            {**sample_post(1), "subject": "first"},
            {**sample_post(1), "subject": "second", "title": "latest", "upvotes": 12},
        ], self.first)
        self.assert_counts(1, "first")
        self.assertEqual(self.client.query("SELECT title, upvotes FROM posts"), [{"title": "latest", "upvotes": 12}])

    def test_bootstrap_only_updates_changed_configuration(self):
        before = self.client.query("SELECT * FROM sources WHERE source_key=?", [self.target.key])
        upsert_source(self.client, self.target, self.later)
        self.assertEqual(self.client.query("SELECT * FROM sources WHERE source_key=?", [self.target.key]), before)
        changed = replace(self.target, min_upvotes=self.target.min_upvotes + 1)
        upsert_source(self.client, changed, self.later)
        row = self.client.query("SELECT * FROM sources WHERE source_key=?", [self.target.key])[0]
        self.assertEqual(row["min_upvotes"], changed.min_upvotes)
        self.assertEqual(row["updated_at"], self.later)
