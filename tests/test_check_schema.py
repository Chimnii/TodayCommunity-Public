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
            ["source_key", "oldest_post_id", "newest_post_id"],
            report["tables"]["coverage_intervals"]["primary_key"],
        )
        self.assertEqual(
            ["source_key", "post_id"],
            report["tables"]["coverage_absences"]["primary_key"],
        )
        subject_column = next(
            row
            for row in client.query("PRAGMA table_info(posts)")
            if row["name"] == "subject"
        )
        self.assertEqual(subject_column["type"], "TEXT")
        self.assertEqual(subject_column["notnull"], 1)
        self.assertEqual(subject_column["dflt_value"], "''")

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
