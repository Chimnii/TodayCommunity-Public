from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from typing import Iterable, Optional

from crawler.jobs.check_schema import (
    SchemaValidationError,
    inspect_schema,
    validate_schema,
)


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
UTC_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "cloudflare"
    / "migrations"
    / "011_canonical_utc_post_times.sql"
)
UTC_MIGRATION = UTC_MIGRATION_PATH.read_text(encoding="utf-8")
ARCHIVE_FILTER_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "cloudflare"
    / "migrations"
    / "012_secret_link_archive_filters.sql"
)
ARCHIVE_FILTER_MIGRATION = ARCHIVE_FILTER_MIGRATION_PATH.read_text(encoding="utf-8")
ARCHIVE_STATS_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "cloudflare"
    / "migrations"
    / "013_archive_stats.sql"
)
ARCHIVE_STATS_MIGRATION = ARCHIVE_STATS_MIGRATION_PATH.read_text(encoding="utf-8")
ARCHIVE_KEYSET_MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "cloudflare"
    / "migrations"
    / "014_archive_keyset_indexes.sql"
)
ARCHIVE_KEYSET_MIGRATION = ARCHIVE_KEYSET_MIGRATION_PATH.read_text(encoding="utf-8")
LEGACY_ARCHIVE_INDEX_DEFINITIONS = """
DROP INDEX IF EXISTS idx_posts_archive_created_at;
DROP INDEX IF EXISTS idx_posts_archive_upvotes;
DROP INDEX IF EXISTS idx_posts_archive_comments;

CREATE INDEX idx_posts_archive_created_at
  ON posts (archive_key, created_at DESC);
CREATE INDEX idx_posts_archive_upvotes
  ON posts (archive_key, upvotes DESC);
CREATE INDEX idx_posts_archive_comments
  ON posts (archive_key, comments DESC);
"""
POST_TIME_METADATA_DEFINITION = """  created_at_basis TEXT NOT NULL DEFAULT 'source'
    CHECK (created_at_basis IN ('source', 'first_seen')),
  created_at_precision TEXT NOT NULL DEFAULT 'second'
    CHECK (created_at_precision IN ('second', 'minute', 'date')),
"""
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

    def test_secret_link_archive_filter_migration_is_idempotent(self) -> None:
        marker = "CREATE TABLE IF NOT EXISTS auth_secret_link_archive_filters"
        self.assertIn(marker, SCHEMA)
        client = SqliteClient(SCHEMA.split(marker, 1)[0])

        client.connection.executescript(ARCHIVE_FILTER_MIGRATION)
        client.connection.executescript(ARCHIVE_FILTER_MIGRATION)

        columns = {
            row["name"]
            for row in client.query(
                "PRAGMA table_info(auth_secret_link_archive_filters)"
            )
        }
        self.assertEqual(
            {
                "secret_link_id",
                "excluded_archive_keys_json",
                "updated_at",
            },
            columns,
        )
        with self.assertRaises(sqlite3.IntegrityError):
            client.query(
                """
                INSERT INTO auth_secret_link_archive_filters (
                  secret_link_id, excluded_archive_keys_json, updated_at
                ) VALUES (1, 'not-json', CURRENT_TIMESTAMP)
                """
            )

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

    def test_default_preflight_does_not_scan_post_rows(self) -> None:
        client = SqliteClient()
        calls = []
        original_query = client.query

        def recording_query(sql, params=None):
            calls.append(sql)
            return original_query(sql, params)

        client.query = recording_query
        report = validate_schema(client)

        self.assertTrue(report["valid"])
        self.assertNotIn("timestamp_audit", report["tables"]["posts"])
        self.assertFalse(any("FROM posts" in sql for sql in calls))

    def test_deep_data_audit_detects_archive_stats_drift(self) -> None:
        client = SqliteClient()
        client.query(
            """
            UPDATE archive_stats
            SET active_post_count = 1
            WHERE archive_key = 'dcinside-singularity'
            """
        )

        self.assertTrue(validate_schema(client)["valid"])
        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client, deep_data_audit=True)

        self.assertEqual(
            caught.exception.report["tables"]["archive_stats"]["data_audit"],
            {
                "archive_count_mismatches": 1,
                "subject_count_mismatches": 0,
                "subject_options_mismatches": 0,
            },
        )
        self.assertTrue(
            any(
                "archive statistics do not match active posts" in error
                for error in caught.exception.report["errors"]
            )
        )

    def test_archive_stats_migration_backfills_once_and_caps_subject_options(self) -> None:
        marker = "CREATE TABLE IF NOT EXISTS archive_stats"
        self.assertIn(marker, SCHEMA)
        client = SqliteClient(SCHEMA.split(marker, 1)[0])
        rows = []
        for index in range(105):
            rows.append(
                (
                    "migration-source",
                    "dcinside-singularity",
                    str(index),
                    f"subject-{index:03d}",
                    "active",
                )
            )
        rows.append(
            (
                "migration-source",
                "dcinside-singularity",
                "long-subject",
                "!" * 101,
                "active",
            )
        )
        rows.append(
            (
                "migration-source",
                "dcinside-singularity",
                "deleted",
                "not-active",
                "deleted",
            )
        )
        client.connection.executemany(
            """
            INSERT INTO posts (
              source_key, archive_key, external_post_id, post_url, subject,
              title, created_at, created_at_raw, fetched_at, first_seen_at,
              last_seen_at, qualifies_by, status
            ) VALUES (
              ?, ?, ?, 'https://example.com', ?, 'post',
              '2026-08-29T00:00:00Z', 'raw', '2026-08-29T00:00:00Z',
              '2026-08-29T00:00:00Z', '2026-08-29T00:00:00Z', 'test', ?
            )
            """,
            rows,
        )

        client.connection.executescript(ARCHIVE_STATS_MIGRATION)
        client.connection.executescript(ARCHIVE_STATS_MIGRATION)

        stats = client.query(
            """
            SELECT active_post_count, subject_options_json
            FROM archive_stats WHERE archive_key = 'dcinside-singularity'
            """
        )[0]
        self.assertEqual(stats["active_post_count"], 106)
        subject_options = json.loads(stats["subject_options_json"])
        self.assertEqual(len(subject_options), 100)
        self.assertNotIn("!" * 101, subject_options)
        self.assertEqual(
            client.query(
                """
                SELECT count(*) AS count
                FROM archive_subject_stats
                WHERE archive_key = 'dcinside-singularity'
                """
            ),
            [{"count": 106}],
        )
        audit_report = inspect_schema(client, deep_data_audit=True)
        self.assertEqual(
            audit_report["tables"]["archive_stats"]["data_audit"],
            {
                "archive_count_mismatches": 0,
                "subject_count_mismatches": 0,
                "subject_options_mismatches": 0,
            },
        )

    def test_staged_archive_indexes_accept_legacy_then_final_forms(self) -> None:
        self.assertNotIn(
            "DROP INDEX IF EXISTS idx_posts_archive_created_at",
            ARCHIVE_STATS_MIGRATION,
        )
        self.assertNotIn(
            "CREATE INDEX IF NOT EXISTS idx_posts_archive_created_at",
            ARCHIVE_STATS_MIGRATION,
        )
        client = SqliteClient()
        client.connection.executescript(LEGACY_ARCHIVE_INDEX_DEFINITIONS)

        legacy_report = validate_schema(client)
        self.assertTrue(legacy_report["valid"])
        client.connection.executescript(ARCHIVE_STATS_MIGRATION)
        self.assertTrue(validate_schema(client)["valid"])
        legacy_rows = {
            row["name"]: row
            for row in client.query("PRAGMA index_list(posts)")
            if row["name"].startswith("idx_posts_archive_")
        }
        self.assertEqual(
            {
                name: row["partial"]
                for name, row in legacy_rows.items()
                if name != "idx_posts_archive_canonical"
            },
            {
                "idx_posts_archive_created_at": 0,
                "idx_posts_archive_upvotes": 0,
                "idx_posts_archive_comments": 0,
            },
        )

        client.connection.executescript(ARCHIVE_KEYSET_MIGRATION)

        final_report = validate_schema(client)
        self.assertTrue(final_report["valid"])
        final_rows = {
            row["name"]: row
            for row in client.query("PRAGMA index_list(posts)")
            if row["name"] in {
                "idx_posts_archive_created_at",
                "idx_posts_archive_upvotes",
                "idx_posts_archive_comments",
            }
        }
        self.assertEqual(
            {name: row["partial"] for name, row in final_rows.items()},
            {
                "idx_posts_archive_created_at": 1,
                "idx_posts_archive_upvotes": 1,
                "idx_posts_archive_comments": 1,
            },
        )
        self.assertEqual(
            final_report["tables"]["posts"]["required_indexes"]
            ["idx_posts_archive_upvotes"],
            ["archive_key", "upvotes", "created_at", "id"],
        )

    def test_archive_index_mixed_legacy_partial_shape_fails_preflight(self) -> None:
        client = SqliteClient()
        client.connection.execute("DROP INDEX idx_posts_archive_created_at")
        client.connection.execute(
            """
            CREATE INDEX idx_posts_archive_created_at
            ON posts (archive_key, created_at DESC)
            WHERE status = 'active'
            """
        )

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertTrue(
            any(
                "index 'idx_posts_archive_created_at' must be a non-partial "
                "index on (archive_key, created_at), or partial index on "
                "(archive_key, created_at, id)" in error
                for error in caught.exception.report["errors"]
            )
        )

    def test_required_partial_indexes_reject_wrong_status_predicates(self) -> None:
        cases = (
            (
                "idx_posts_active_created",
                "created_at DESC, id DESC",
            ),
            (
                "idx_posts_archive_created_at",
                "archive_key, created_at DESC, id DESC",
            ),
        )

        for index_name, columns in cases:
            with self.subTest(index=index_name):
                client = SqliteClient()
                client.connection.execute(f"DROP INDEX {index_name}")
                client.connection.execute(
                    f"""
                    CREATE INDEX {index_name}
                    ON posts ({columns})
                    WHERE status = 'deleted'
                    """
                )

                with self.assertRaises(SchemaValidationError) as caught:
                    validate_schema(client)

                self.assertIn(
                    f"table 'posts' index '{index_name}' must use "
                    "WHERE status = 'active'; found WHERE status='deleted'",
                    caught.exception.report["errors"],
                )

    def test_required_partial_predicate_normalizes_sql_whitespace_and_case(self) -> None:
        client = SqliteClient()
        client.connection.execute("DROP INDEX idx_posts_active_created")
        client.connection.execute(
            """
            CREATE INDEX idx_posts_active_created
            ON posts (created_at DESC, id DESC)
            WHERE STATUS    =    'active'
            """
        )

        self.assertTrue(validate_schema(client)["valid"])

    def test_required_indexes_reject_mixed_sort_directions(self) -> None:
        cases = (
            (
                "idx_posts_archive_created_at",
                "archive_key DESC, created_at DESC",
                "",
                "(archive_key ASC, created_at DESC)",
                "(archive_key DESC, created_at DESC)",
            ),
            (
                "idx_posts_archive_upvotes",
                "archive_key, upvotes DESC, created_at ASC, id DESC",
                "WHERE status = 'active'",
                "(archive_key ASC, upvotes DESC, created_at DESC, id DESC)",
                "(archive_key ASC, upvotes DESC, created_at ASC, id DESC)",
            ),
            (
                "idx_posts_active_comments",
                "comments DESC, created_at ASC, id DESC",
                "WHERE status = 'active'",
                "(comments DESC, created_at DESC, id DESC)",
                "(comments DESC, created_at ASC, id DESC)",
            ),
        )

        for index_name, columns, predicate, expected, actual in cases:
            with self.subTest(index=index_name):
                client = SqliteClient()
                client.connection.execute(f"DROP INDEX {index_name}")
                client.connection.execute(
                    f"""
                    CREATE INDEX {index_name}
                    ON posts ({columns})
                    {predicate}
                    """
                )

                with self.assertRaises(SchemaValidationError) as caught:
                    validate_schema(client)

                self.assertIn(
                    f"table 'posts' index '{index_name}' must order {expected}; "
                    f"found {actual}",
                    caught.exception.report["errors"],
                )

    def test_required_indexes_reject_non_binary_key_collations(self) -> None:
        cases = (
            (
                "idx_posts_active_created",
                "created_at COLLATE NOCASE DESC, id DESC",
            ),
            (
                "idx_posts_archive_created_at",
                "archive_key, created_at COLLATE NOCASE DESC, id DESC",
            ),
        )

        for index_name, columns in cases:
            with self.subTest(index=index_name):
                client = SqliteClient()
                client.connection.execute(f"DROP INDEX {index_name}")
                client.connection.execute(
                    f"""
                    CREATE INDEX {index_name}
                    ON posts ({columns})
                    WHERE status = 'active'
                    """
                )

                with self.assertRaises(SchemaValidationError) as caught:
                    validate_schema(client)

                self.assertTrue(
                    any(
                        f"index '{index_name}' must use BINARY collation"
                        in error
                        for error in caught.exception.report["errors"]
                    )
                )

    def test_utc_migration_normalizes_existing_rows_without_losing_source_time(self) -> None:
        self.assertIn(POST_TIME_METADATA_DEFINITION, SCHEMA)
        client = SqliteClient(
            SCHEMA.replace(POST_TIME_METADATA_DEFINITION, "", 1)
        )
        client.query(
            """
            UPDATE archives
            SET created_at = '2026-08-28 12:00:00',
                updated_at = '2026-08-29T09:00:00+09:00'
            WHERE archive_key = 'dcinside-zeus-pride'
            """
        )
        for source_key, archive_key, site_name in (
            ("dcinside-zeus-pride", "dcinside-zeus-pride", "dcinside"),
            ("fmkorea-migration-test", "fmkorea-munich", "fmkorea"),
        ):
            client.query(
                """
                INSERT INTO sources (
                  source_key, archive_key, site_name, board_name, board_url,
                  created_at, updated_at
                ) VALUES (?, ?, ?, 'board', 'https://example.com',
                          '2026-08-28T12:00:00+00:00',
                          '2026-08-29T09:00:00+09:00')
                """,
                [source_key, archive_key, site_name],
            )
        client.query(
            """
            INSERT INTO game_news_candidates (
              canonical_url, url_sha256, source_key, title, publisher, topic,
              summary, published_at, published_at_raw, discovery_reason,
              first_seen_at, last_seen_at, first_run_id, last_run_id
            ) VALUES (
              'https://www.inven.co.kr/article/date-only', ?,
              'game-news-inven', 'date article', '인벤', 'industry', 'summary',
              '2026-08-29T00:00:00Z', '2026-08-29', 'reason',
              '2026-08-29T00:00:00Z', '2026-08-29T00:00:00Z', 'run-1', 'run-1'
            )
            """,
            ["a" * 64],
        )
        posts = (
            (
                "dcinside-zeus-pride", "dcinside-zeus-pride", "dcinside:z:1",
                "1", "26.08.28", "2026-08-28T23:59:59+09:00",
                "2026-08-29T09:00:00+09:00",
            ),
            (
                "fmkorea-migration-test", "fmkorea-munich", "fmkorea:2",
                "2", "1분 전", "2026-08-29T09:31:37+09:00",
                "2026-08-29T09:32:00+09:00",
            ),
            (
                "game-news-inven", "game-news", f"game-news:{'a' * 64}",
                "3", "2026-08-29", "2026-08-29T00:00:00Z",
                "2026-08-29T00:00:00Z",
            ),
            (
                "game-news-inven", "game-news", f"game-news:{'b' * 64}",
                "4", "발행 시각 미상", "2026-08-29T01:02:03+00:00",
                "2026-08-29T01:02:03+00:00",
            ),
        )
        client.connection.executemany(
            """
            INSERT INTO posts (
              source_key, archive_key, canonical_post_key, external_post_id,
              post_url, title, created_at_raw, created_at, fetched_at,
              first_seen_at, last_seen_at, qualifies_by
            ) VALUES (?, ?, ?, ?, 'https://example.com/post', 'post', ?, ?, ?, ?, ?,
                      'migration-test')
            """,
            [
                (*row[:5], row[5], row[6], row[6], row[6])
                for row in posts
            ],
        )
        client.query(
            """
            INSERT INTO crawl_runs (
              source_key, run_type, status, started_at, finished_at
            ) VALUES (
              'dcinside-zeus-pride', 'incremental', 'success',
              '2026-08-29T09:00:00+09:00', '2026-08-29T00:01:00+00:00'
            )
            """
        )
        client.query(
            """
            INSERT INTO source_state (
              source_key, backfill_anchor_created_at, blocked_until,
              last_blocked_at, created_at, updated_at
            ) VALUES (
              'dcinside-zeus-pride', '2026-08-28T23:59:59+09:00', NULL,
              '2026-08-29T08:00:00+09:00', '2026-08-28 12:00:00',
              '2026-08-29T09:00:00+09:00'
            )
            """
        )
        client.query(
            """
            INSERT INTO coverage_intervals (
              source_key, oldest_post_id, newest_post_id, oldest_created_at,
              newest_created_at, checked_at, created_at, updated_at
            ) VALUES (
              'dcinside-zeus-pride', 10, 20, '',
              '2026-08-29T09:00:00+09:00', '2026-08-29T00:01:00+00:00',
              '2026-08-29 00:00:00', '2026-08-29T09:02:00+09:00'
            )
            """
        )
        client.query(
            """
            INSERT INTO coverage_absences (
              source_key, post_id, newer_page, older_page,
              newer_boundary_post_id, older_boundary_post_id, checked_at,
              created_at, updated_at
            ) VALUES (
              'dcinside-zeus-pride', 15, 1, 2, 20, 10,
              '2026-08-29T09:00:00+09:00', '2026-08-29 00:00:00',
              '2026-08-29T09:02:00+09:00'
            )
            """
        )
        before_count = client.query("SELECT COUNT(*) AS count FROM posts")[0]["count"]

        client.connection.executescript(UTC_MIGRATION)

        self.assertEqual(
            client.query("SELECT COUNT(*) AS count FROM posts")[0]["count"],
            before_count,
        )
        self.assertEqual(
            client.query(
                """
                SELECT external_post_id, created_at, created_at_raw,
                       created_at_basis, created_at_precision
                FROM posts
                ORDER BY id
                """
            ),
            [
                {
                    "external_post_id": "1",
                    "created_at": "2026-08-28T14:59:59Z",
                    "created_at_raw": "26.08.28",
                    "created_at_basis": "source",
                    "created_at_precision": "date",
                },
                {
                    "external_post_id": "2",
                    "created_at": "2026-08-29T00:31:37Z",
                    "created_at_raw": "1분 전",
                    "created_at_basis": "source",
                    "created_at_precision": "minute",
                },
                {
                    "external_post_id": "3",
                    "created_at": "2026-08-29T00:00:00Z",
                    "created_at_raw": "2026-08-29",
                    "created_at_basis": "source",
                    "created_at_precision": "date",
                },
                {
                    "external_post_id": "4",
                    "created_at": "2026-08-29T01:02:03Z",
                    "created_at_raw": "발행 시각 미상",
                    "created_at_basis": "first_seen",
                    "created_at_precision": "second",
                },
            ],
        )
        self.assertEqual(
            client.query(
                "SELECT external_post_id FROM posts ORDER BY created_at DESC"
            ),
            [
                {"external_post_id": "4"},
                {"external_post_id": "2"},
                {"external_post_id": "3"},
                {"external_post_id": "1"},
            ],
        )
        self.assertEqual(
            client.query(
                """
                SELECT started_at, finished_at FROM crawl_runs
                WHERE source_key = 'dcinside-zeus-pride'
                """
            ),
            [
                {
                    "started_at": "2026-08-29T00:00:00Z",
                    "finished_at": "2026-08-29T00:01:00Z",
                }
            ],
        )
        self.assertEqual(
            client.query(
                """
                SELECT backfill_anchor_created_at, blocked_until, last_blocked_at,
                       created_at, updated_at
                FROM source_state WHERE source_key = 'dcinside-zeus-pride'
                """
            ),
            [
                {
                    "backfill_anchor_created_at": "2026-08-28T14:59:59Z",
                    "blocked_until": None,
                    "last_blocked_at": "2026-08-28T23:00:00Z",
                    "created_at": "2026-08-28T12:00:00Z",
                    "updated_at": "2026-08-29T00:00:00Z",
                }
            ],
        )
        self.assertEqual(
            client.query(
                """
                SELECT oldest_created_at, newest_created_at, checked_at,
                       created_at, updated_at
                FROM coverage_intervals
                WHERE source_key = 'dcinside-zeus-pride'
                """
            ),
            [
                {
                    "oldest_created_at": "",
                    "newest_created_at": "2026-08-29T00:00:00Z",
                    "checked_at": "2026-08-29T00:01:00Z",
                    "created_at": "2026-08-29T00:00:00Z",
                    "updated_at": "2026-08-29T00:02:00Z",
                }
            ],
        )
        client.connection.executescript(ARCHIVE_STATS_MIGRATION)
        report = validate_schema(client, deep_data_audit=True)
        self.assertTrue(report["valid"])
        self.assertEqual(
            {
                "invalid_created_at": 0,
                "invalid_fetched_at": 0,
                "invalid_first_seen_at": 0,
                "invalid_last_seen_at": 0,
                "invalid_basis": 0,
                "invalid_precision": 0,
            },
            report["tables"]["posts"]["timestamp_audit"],
        )

    def test_noncanonical_post_timestamp_fails_preflight(self) -> None:
        client = SqliteClient()
        client.query(
            """
            INSERT INTO sources (
              source_key, archive_key, site_name, board_name, board_url
            ) VALUES (
              'timestamp-test', 'dcinside-singularity', 'test', 'test',
              'https://example.com'
            )
            """
        )
        client.query(
            """
            INSERT INTO posts (
              source_key, archive_key, external_post_id, post_url, title,
              created_at, created_at_raw, fetched_at, first_seen_at,
              last_seen_at, qualifies_by
            ) VALUES (
              'timestamp-test', 'dcinside-singularity', '1',
              'https://example.com/1', 'post', 'xxxxxxxxxxxxxxxxxxxx',
              'raw', '2026-08-29T00:00:00Z', '2026-08-29T00:00:00Z',
              '2026-08-29T00:00:00Z', 'test'
            )
            """
        )

        client.connection.executescript(ARCHIVE_STATS_MIGRATION)
        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client, deep_data_audit=True)

        self.assertTrue(
            any(
                "contains non-canonical timestamps" in error
                for error in caught.exception.report["errors"]
            )
        )

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

    def test_all_archive_exclusion_query_searches_the_archive_index(self) -> None:
        client = SqliteClient()

        plan = client.query(
            """
            EXPLAIN QUERY PLAN
            SELECT id
            FROM posts
            WHERE EXISTS (
              SELECT 1
              FROM archives
              WHERE archives.archive_key = posts.archive_key
                AND is_public = 1
                AND archive_key NOT IN (?, ?)
            )
              AND posts.status = 'active'
            ORDER BY created_at DESC, id DESC
            LIMIT 30
            """,
            ["game-news", "dcinside-agent-stack"],
        )

        self.assertTrue(
            any(
                "idx_posts_active_created" in row["detail"]
                for row in plan
            )
        )
        self.assertFalse(any("TEMP B-TREE" in row["detail"] for row in plan))

    def test_archive_keyset_plans_use_active_partial_sort_indexes(self) -> None:
        client = SqliteClient()
        cases = (
            (
                "archive_key = ? AND status = 'active'",
                "created_at DESC, id DESC",
                "idx_posts_archive_created_at",
                ["dcinside-singularity"],
            ),
            (
                "archive_key = ? AND status = 'active'",
                "upvotes DESC, created_at DESC, id DESC",
                "idx_posts_archive_upvotes",
                ["dcinside-singularity"],
            ),
            (
                "archive_key = ? AND status = 'active'",
                "comments DESC, created_at DESC, id DESC",
                "idx_posts_archive_comments",
                ["dcinside-singularity"],
            ),
            (
                "status = 'active'",
                "created_at DESC, id DESC",
                "idx_posts_active_created",
                [],
            ),
            (
                "status = 'active'",
                "upvotes DESC, created_at DESC, id DESC",
                "idx_posts_active_upvotes",
                [],
            ),
            (
                "status = 'active'",
                "comments DESC, created_at DESC, id DESC",
                "idx_posts_active_comments",
                [],
            ),
        )

        for where_sql, order_sql, index_name, params in cases:
            with self.subTest(index=index_name):
                plan = client.query(
                    f"""
                    EXPLAIN QUERY PLAN
                    SELECT id
                    FROM posts
                    WHERE {where_sql}
                    ORDER BY {order_sql}
                    LIMIT 51
                    """,
                    params,
                )
                details = "\n".join(row["detail"] for row in plan)
                self.assertIn(index_name, details)
                self.assertNotIn("TEMP B-TREE", details)

    def test_missing_active_keyset_index_fails_preflight(self) -> None:
        definition = """CREATE INDEX IF NOT EXISTS idx_posts_active_created
  ON posts (created_at DESC, id DESC)
  WHERE status = 'active';

"""
        self.assertIn(definition, SCHEMA)
        client = SqliteClient(SCHEMA.replace(definition, "", 1))

        with self.assertRaises(SchemaValidationError) as caught:
            validate_schema(client)

        self.assertIn(
            "table 'posts' is missing required index "
            "'idx_posts_active_created' on (created_at, id)",
            caught.exception.report["errors"],
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
