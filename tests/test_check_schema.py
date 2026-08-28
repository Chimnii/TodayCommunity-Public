from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from typing import Iterable, Optional

from crawler.jobs.check_schema import SchemaValidationError, validate_schema


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "cloudflare" / "schema.sql"
SCHEMA = SCHEMA_PATH.read_text(encoding="utf-8")
SUBJECT_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "cloudflare"
    / "migrations"
    / "001_add_posts_subject.sql"
)
SUBJECT_MIGRATION = SUBJECT_MIGRATION_PATH.read_text(encoding="utf-8")
MULTI_ARCHIVE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "cloudflare"
    / "migrations"
    / "002_multi_archive.sql"
)
MULTI_ARCHIVE_MIGRATION = MULTI_ARCHIVE_MIGRATION_PATH.read_text(encoding="utf-8")
AUTH_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "cloudflare"
    / "migrations"
    / "007_owner_auth.sql"
)
AUTH_MIGRATION = AUTH_MIGRATION_PATH.read_text(encoding="utf-8")
D1_USAGE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "cloudflare"
    / "migrations"
    / "009_d1_usage_optimization.sql"
)
D1_USAGE_MIGRATION = D1_USAGE_MIGRATION_PATH.read_text(encoding="utf-8")
ZEUS_ARCHIVE_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "cloudflare"
    / "migrations"
    / "010_add_zeus_pride_archive.sql"
)
ZEUS_ARCHIVE_MIGRATION = ZEUS_ARCHIVE_MIGRATION_PATH.read_text(encoding="utf-8")
CRAWL_RUN_INDEX_DEFINITIONS = """CREATE INDEX IF NOT EXISTS idx_crawl_runs_source_status_id
  ON crawl_runs (source_key, status, id DESC);

CREATE INDEX IF NOT EXISTS idx_crawl_runs_source_id
  ON crawl_runs (source_key, id DESC);

"""


class SqliteClient:
    def __init__(self, schema: str = SCHEMA) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(schema)

    def query(self, sql: str, params: Optional[Iterable[object]] = None):
        cursor = self.connection.execute(sql, list(params or []))
        if cursor.description is None:
            self.connection.commit()
            return []
        columns = [item[0] for item in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


class SchemaPreflightTests(unittest.TestCase):
    def test_zeus_archive_seed_and_migration_are_idempotent(self) -> None:
        client = SqliteClient()
        expected = {
            "archive_key": "dcinside-zeus-pride",
            "display_name": "제우스 오만의 신",
            "description": "디시인사이드 제우스 오만의 신 갤러리 인기글",
            "display_order": 25,
            "is_public": 1,
            "content_kind": "community",
        }

        rows = client.query(
            """
            SELECT archive_key, display_name, description, display_order,
                   is_public, content_kind
            FROM archives
            WHERE archive_key = 'dcinside-zeus-pride'
            """
        )
        self.assertEqual(rows, [expected])

        client.query(
            """
            UPDATE archives
            SET display_name = 'old', description = 'old', display_order = 999,
                is_public = 0
            WHERE archive_key = 'dcinside-zeus-pride'
            """
        )
        client.connection.executescript(ZEUS_ARCHIVE_MIGRATION)
        client.connection.executescript(ZEUS_ARCHIVE_MIGRATION)

        self.assertEqual(
            client.query(
                """
                SELECT archive_key, display_name, description, display_order,
                       is_public, content_kind
                FROM archives
                WHERE archive_key = 'dcinside-zeus-pride'
                """
            ),
            [expected],
        )

    def test_owner_auth_schema_stores_only_hashed_link_credentials(self) -> None:
        client = SqliteClient()
        columns = {
            row["name"] for row in client.query("PRAGMA table_info(auth_secret_links)")
        }

        self.assertIn("token_hash", columns)
        self.assertNotIn("token", columns)
        self.assertNotIn("password", columns)
        with self.assertRaises(sqlite3.IntegrityError):
            client.query(
                """
                INSERT INTO auth_secret_links (label, token_hash, created_at)
                VALUES ('invalid', 'raw-secret-token', CURRENT_TIMESTAMP)
                """
            )

    def test_owner_auth_migration_applies_to_the_previous_schema(self) -> None:
        marker = "CREATE TABLE IF NOT EXISTS auth_secret_links"
        self.assertIn(marker, SCHEMA)
        client = SqliteClient(SCHEMA.split(marker, 1)[0])

        client.connection.executescript(AUTH_MIGRATION)

        tables = {
            row["name"]
            for row in client.query(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name LIKE 'auth_%'
                """
            )
        }
        self.assertEqual({"auth_login_limits", "auth_secret_links"}, tables)

    def test_current_schema_has_required_columns_and_keys(self) -> None:
        client = SqliteClient()
        report = validate_schema(client)

        self.assertTrue(report["valid"])
        self.assertEqual([], report["errors"])
        self.assertEqual(
            ["source_key", "external_post_id"],
            next(
                key
                for key in report["tables"]["posts"]["unique_keys"]
                if key == ["source_key", "external_post_id"]
            ),
        )
        self.assertEqual(
            ["archive_key", "canonical_post_key"],
            next(
                key
                for key in report["tables"]["posts"]["unique_keys"]
                if key == ["archive_key", "canonical_post_key"]
            ),
        )
        self.assertEqual(
            ["archive_key"],
            report["tables"]["archives"]["primary_key"],
        )
        self.assertEqual(
            ["source_key", "oldest_post_id", "newest_post_id"],
            report["tables"]["coverage_intervals"]["primary_key"],
        )
        self.assertEqual(
            ["source_key", "post_id"],
            report["tables"]["coverage_absences"]["primary_key"],
        )
        self.assertEqual(
            {
                "idx_crawl_runs_source_status_id": ["source_key", "status", "id"],
                "idx_crawl_runs_source_id": ["source_key", "id"],
            },
            report["tables"]["crawl_runs"]["required_indexes"],
        )
        subject_column = next(
            row
            for row in client.query("PRAGMA table_info(posts)")
            if row["name"] == "subject"
        )
        self.assertEqual(subject_column["type"], "TEXT")
        self.assertEqual(subject_column["notnull"], 1)
        self.assertEqual(subject_column["dflt_value"], "''")
        canonical_column = next(
            row
            for row in client.query("PRAGMA table_info(posts)")
            if row["name"] == "canonical_post_key"
        )
        self.assertEqual(canonical_column["type"], "TEXT")
        self.assertEqual(canonical_column["notnull"], 0)
        self.assertIsNone(canonical_column["dflt_value"])

    def test_multi_archive_migration_backfills_existing_singularity_rows(self) -> None:
        client = SqliteClient(
            """
            CREATE TABLE sources (
              source_key TEXT PRIMARY KEY,
              site_name TEXT NOT NULL,
              board_name TEXT NOT NULL,
              board_url TEXT NOT NULL,
              min_upvotes INTEGER NOT NULL DEFAULT 0,
              min_comments INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE posts (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_key TEXT NOT NULL,
              external_post_id TEXT NOT NULL,
              post_url TEXT NOT NULL,
              subject TEXT NOT NULL DEFAULT '',
              title TEXT NOT NULL,
              created_at TEXT NOT NULL,
              created_at_raw TEXT NOT NULL,
              upvotes INTEGER NOT NULL DEFAULT 0,
              comments INTEGER NOT NULL DEFAULT 0,
              fetched_at TEXT NOT NULL,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              qualifies_by TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'active',
              UNIQUE(source_key, external_post_id)
            );
            """
        )
        client.query(
            """
            INSERT INTO sources (source_key, site_name, board_name, board_url)
            VALUES (
              'dcinside-singularity', 'dcinside', 'board',
              'https://gall.dcinside.com/'
            )
            """
        )
        client.query(
            """
            INSERT INTO posts (
              source_key, external_post_id, post_url, title, created_at,
              created_at_raw, fetched_at, first_seen_at, last_seen_at, qualifies_by
            ) VALUES (
              'dcinside-singularity', '123', 'https://example.com/123', 'old',
              '2026-07-19T00:00:00Z', '2026-07-19', '2026-07-19T00:00:00Z',
              '2026-07-19T00:00:00Z', '2026-07-19T00:00:00Z', 'upvotes'
            )
            """
        )

        client.connection.executescript(MULTI_ARCHIVE_MIGRATION)

        self.assertEqual(
            client.query("SELECT archive_key FROM sources"),
            [{"archive_key": "dcinside-singularity"}],
        )
        self.assertEqual(
            client.query(
                "SELECT archive_key, canonical_post_key FROM posts"
            ),
            [
                {
                    "archive_key": "dcinside-singularity",
                    "canonical_post_key": "dcinside:thesingularity:123",
                }
            ],
        )
        unique_indexes = client.query("PRAGMA index_list(posts)")
        self.assertTrue(
            any(
                row["name"] == "idx_posts_archive_canonical" and row["unique"] == 1
                for row in unique_indexes
            )
        )

    def test_subject_migration_keeps_existing_posts_with_an_empty_value(self) -> None:
        subject_definition = "  subject TEXT NOT NULL DEFAULT '',\n"
        self.assertIn(subject_definition, SCHEMA)
        client = SqliteClient(SCHEMA.replace(subject_definition, "", 1))
        client.query(
            """
            INSERT INTO sources (source_key, site_name, board_name, board_url)
            VALUES ('source', 'site', 'board', 'https://example.com')
            """
        )
        client.query(
            """
            INSERT INTO posts (
              source_key, external_post_id, post_url, title, created_at,
              created_at_raw, fetched_at, first_seen_at, last_seen_at, qualifies_by
            ) VALUES (
              'source', '1', 'https://example.com/1', 'old post',
              '2026-07-19T00:00:00Z', '2026-07-19', '2026-07-19T00:00:00Z',
              '2026-07-19T00:00:00Z', '2026-07-19T00:00:00Z', 'upvotes'
            )
            """
        )

        client.connection.executescript(SUBJECT_MIGRATION)

        self.assertTrue(validate_schema(client)["valid"])
        self.assertEqual(client.query("SELECT subject FROM posts"), [{"subject": ""}])

    def test_missing_posts_subject_column_fails_preflight(self) -> None:
        subject_definition = "  subject TEXT NOT NULL DEFAULT '',\n"
        self.assertIn(subject_definition, SCHEMA)
        client = SqliteClient(SCHEMA.replace(subject_definition, "", 1))

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertIn(
            "table 'posts' is missing columns: subject",
            caught.exception.report["errors"],
        )

    def test_wrong_posts_subject_definition_fails_preflight(self) -> None:
        subject_definition = "  subject TEXT NOT NULL DEFAULT '',\n"
        self.assertIn(subject_definition, SCHEMA)
        client = SqliteClient(
            SCHEMA.replace(subject_definition, "  subject TEXT DEFAULT NULL,\n", 1)
        )

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertTrue(
            any(
                "table 'posts' column 'subject' must be" in error
                for error in caught.exception.report["errors"]
            )
        )

    def test_missing_table_fails_with_clear_error(self) -> None:
        client = SqliteClient()
        client.connection.execute("DROP TABLE coverage_intervals")

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertIn(
            "missing required table: coverage_intervals",
            caught.exception.report["errors"],
        )

    def test_missing_archives_table_fails_with_clear_error(self) -> None:
        client = SqliteClient()
        client.connection.execute("PRAGMA foreign_keys = OFF")
        client.connection.execute("DROP TABLE archives")

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertIn(
            "missing required table: archives",
            caught.exception.report["errors"],
        )

    def test_missing_absence_table_fails_with_clear_error(self) -> None:
        client = SqliteClient()
        client.connection.execute("DROP TABLE coverage_absences")

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertIn(
            "missing required table: coverage_absences",
            caught.exception.report["errors"],
        )

    def test_missing_absence_runtime_column_fails(self) -> None:
        fragment = (
            "  older_boundary_post_id INTEGER NOT NULL,\n"
            "  checked_at TEXT NOT NULL,\n"
            "  created_at TEXT NOT NULL,\n"
        )
        self.assertIn(fragment, SCHEMA)
        client = SqliteClient(
            SCHEMA.replace(
                fragment,
                "  older_boundary_post_id INTEGER NOT NULL,\n"
                "  checked_at TEXT NOT NULL,\n",
                1,
            )
        )

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertIn(
            "table 'coverage_absences' is missing columns: created_at",
            caught.exception.report["errors"],
        )

    def test_missing_runtime_column_fails(self) -> None:
        fragment = "  blocked_until TEXT,\n"
        self.assertIn(fragment, SCHEMA)
        client = SqliteClient(SCHEMA.replace(fragment, "", 1))

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertIn(
            "table 'source_state' is missing columns: blocked_until",
            caught.exception.report["errors"],
        )

    def test_missing_posts_unique_constraint_fails(self) -> None:
        fragment = "  UNIQUE(source_key, external_post_id),\n"
        self.assertIn(fragment, SCHEMA)
        client = SqliteClient(SCHEMA.replace(fragment, "", 1))

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertTrue(
            any(
                "table 'posts' must have UNIQUE (source_key, external_post_id)"
                in error
                for error in caught.exception.report["errors"]
            )
        )

    def test_d1_usage_migration_adds_required_crawl_run_indexes(self) -> None:
        self.assertIn(CRAWL_RUN_INDEX_DEFINITIONS, SCHEMA)
        client = SqliteClient(SCHEMA.replace(CRAWL_RUN_INDEX_DEFINITIONS, "", 1))

        with self.assertRaises(SchemaValidationError):
            validate_schema(client)

        client.connection.executescript(D1_USAGE_MIGRATION)
        client.connection.executescript(D1_USAGE_MIGRATION)

        self.assertTrue(validate_schema(client)["valid"])

    def test_missing_crawl_run_performance_index_fails_preflight(self) -> None:
        missing_definition = """CREATE INDEX IF NOT EXISTS idx_crawl_runs_source_status_id
  ON crawl_runs (source_key, status, id DESC);

"""
        self.assertIn(missing_definition, SCHEMA)
        client = SqliteClient(SCHEMA.replace(missing_definition, "", 1))

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertIn(
            "table 'crawl_runs' is missing required index "
            "'idx_crawl_runs_source_status_id' on (source_key, status, id)",
            caught.exception.report["errors"],
        )

    def test_crawl_run_lookup_plans_use_required_indexes(self) -> None:
        client = SqliteClient()

        cooldown_plan = client.query(
            """
            EXPLAIN QUERY PLAN
            SELECT started_at, finished_at
            FROM crawl_runs
            WHERE source_key = ? AND status = 'blocked'
            ORDER BY id DESC
            LIMIT 1
            """,
            ["dcinside-singularity"],
        )
        recent_plan = client.query(
            """
            EXPLAIN QUERY PLAN
            SELECT id
            FROM crawl_runs
            WHERE source_key = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            ["dcinside-singularity"],
        )
        archive_recent_plan = client.query(
            """
            EXPLAIN QUERY PLAN
            WITH archive_sources AS (
              SELECT source_key
              FROM sources
              WHERE archive_key = ?
            )
            SELECT runs.id
            FROM archive_sources AS sources
            INNER JOIN crawl_runs AS runs
              ON runs.id IN (
                SELECT source_runs.id
                FROM crawl_runs AS source_runs
                WHERE source_runs.source_key = sources.source_key
                ORDER BY source_runs.id DESC
                LIMIT 10
              )
            ORDER BY runs.id DESC
            LIMIT 10
            """,
            ["dcinside-singularity"],
        )

        self.assertTrue(
            any(
                "idx_crawl_runs_source_status_id" in row["detail"]
                for row in cooldown_plan
            )
        )
        self.assertTrue(
            any("idx_crawl_runs_source_id" in row["detail"] for row in recent_plan)
        )
        self.assertTrue(
            any(
                "idx_crawl_runs_source_id" in row["detail"]
                for row in archive_recent_plan
            )
        )
        self.assertFalse(
            any("SCAN source_runs" in row["detail"] for row in archive_recent_plan)
        )

    def test_missing_archive_canonical_unique_constraint_fails(self) -> None:
        fragment = "  UNIQUE(archive_key, canonical_post_key),\n"
        self.assertIn(fragment, SCHEMA)
        client = SqliteClient(SCHEMA.replace(fragment, "", 1))

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertTrue(
            any(
                "table 'posts' must have UNIQUE "
                "(archive_key, canonical_post_key)" in error
                for error in caught.exception.report["errors"]
            )
        )

    def test_partial_unique_index_does_not_satisfy_runtime_conflict_key(self) -> None:
        fragment = "  UNIQUE(source_key, external_post_id),\n"
        self.assertIn(fragment, SCHEMA)
        client = SqliteClient(SCHEMA.replace(fragment, "", 1))
        client.connection.execute(
            """
            CREATE UNIQUE INDEX idx_posts_partial_identity
            ON posts (source_key, external_post_id)
            WHERE status = 'active'
            """
        )

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertTrue(
            any(
                "table 'posts' must have UNIQUE (source_key, external_post_id)"
                in error
                for error in caught.exception.report["errors"]
            )
        )

    def test_wrong_coverage_primary_key_fails(self) -> None:
        expected = "  PRIMARY KEY (source_key, oldest_post_id, newest_post_id),\n"
        replacement = "  PRIMARY KEY (source_key, oldest_post_id),\n"
        self.assertIn(expected, SCHEMA)
        client = SqliteClient(SCHEMA.replace(expected, replacement, 1))

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertIn(
            "table 'coverage_intervals' must have PRIMARY KEY "
            "(source_key, oldest_post_id, newest_post_id); "
            "found (source_key, oldest_post_id)",
            caught.exception.report["errors"],
        )

    def test_wrong_absence_primary_key_fails(self) -> None:
        expected = "  PRIMARY KEY (source_key, post_id),\n"
        replacement = "  PRIMARY KEY (post_id),\n"
        self.assertIn(expected, SCHEMA)
        client = SqliteClient(SCHEMA.replace(expected, replacement, 1))

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertIn(
            "table 'coverage_absences' must have PRIMARY KEY "
            "(source_key, post_id); found (post_id)",
            caught.exception.report["errors"],
        )


if __name__ == "__main__":
    unittest.main()
