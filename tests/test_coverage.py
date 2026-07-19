from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path
from typing import Iterable, Optional

from crawler.coverage import (
    CoverageAbsence,
    CoverageAbsenceRepository,
    CoverageInterval,
    CoverageRepository,
    absence_to_interval,
    contains_post_id,
    merge_scanned_interval,
    normalize_effective_coverage,
    normalize_intervals,
    validate_numeric_post_id,
)


SOURCE_KEY = "dcinside-singularity"
FIXED_TIME = "2026-07-16T00:00:00+00:00"


def interval(oldest: object, newest: object) -> CoverageInterval:
    return CoverageInterval(
        source_key=SOURCE_KEY,
        oldest_post_id=oldest,
        newest_post_id=newest,
        checked_at=FIXED_TIME,
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )


def absence(
    post_id: object = 201,
    *,
    newer_page: object = 10,
    older_page: object = 11,
    newer_boundary_post_id: object = 202,
    older_boundary_post_id: object = 200,
    checked_at: str = FIXED_TIME,
) -> CoverageAbsence:
    return CoverageAbsence(
        source_key=SOURCE_KEY,
        post_id=post_id,
        newer_page=newer_page,
        older_page=older_page,
        newer_boundary_post_id=newer_boundary_post_id,
        older_boundary_post_id=older_boundary_post_id,
        checked_at=checked_at,
        created_at=FIXED_TIME,
        updated_at=checked_at,
    )


class SqliteD1Client:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection
        self.operations = []

    def query(self, sql: str, params: Optional[Iterable[object]] = None):
        normalized_sql = " ".join(sql.split())
        self.operations.append(normalized_sql.split(" ", 1)[0].upper())
        cursor = self.connection.execute(sql, list(params or []))
        if cursor.description:
            columns = [item[0] for item in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        self.connection.commit()
        return []


class CoverageModelTests(unittest.TestCase):
    def test_numeric_post_id_validation(self) -> None:
        self.assertEqual(validate_numeric_post_id("00123"), 123)
        self.assertEqual(validate_numeric_post_id(123), 123)
        for invalid in (True, 0, -1, "", "12.3", "abc", 12.0, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_numeric_post_id(invalid)

    def test_contains_is_inclusive(self) -> None:
        coverage = interval(100, 200)
        self.assertTrue(coverage.contains("100"))
        self.assertTrue(coverage.contains(200))
        self.assertTrue(contains_post_id([coverage], 150))
        self.assertFalse(contains_post_id([coverage], 201))

    def test_normalize_single_interval(self) -> None:
        self.assertEqual(normalize_intervals([interval(100, 200)]), [interval(100, 200)])

    def test_normalize_overlap_and_containment(self) -> None:
        normalized = normalize_intervals(
            [interval(100, 200), interval(150, 250), interval(175, 190)]
        )
        self.assertEqual([(item.oldest_post_id, item.newest_post_id) for item in normalized], [(100, 250)])

    def test_normalize_exact_adjacency_but_not_a_numeric_gap(self) -> None:
        normalized = normalize_intervals(
            [interval(100, 200), interval(201, 250), interval(252, 300)]
        )
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in normalized],
            [(100, 250), (252, 300)],
        )

    def test_actual_scanned_bridge_connects_two_existing_ranges(self) -> None:
        result = merge_scanned_interval(
            [interval(100, 200), interval(300, 400)],
            interval(200, 300),
        )
        self.assertEqual(
            (result.merged.oldest_post_id, result.merged.newest_post_id),
            (100, 400),
        )
        self.assertEqual(len(result.superseded), 2)

    def test_unrelated_existing_ranges_are_not_rewritten_by_new_scan(self) -> None:
        existing = [interval(100, 150), interval(151, 200), interval(300, 350)]
        result = merge_scanned_interval(existing, interval(400, 450))
        self.assertEqual(
            (result.merged.oldest_post_id, result.merged.newest_post_id),
            (400, 450),
        )
        self.assertEqual(result.superseded, ())

    def test_absence_requires_adjacent_pages_and_strict_id_boundaries(self) -> None:
        valid = absence()
        self.assertEqual(valid.post_id, 201)

        invalid_values = (
            {"newer_page": 0},
            {"older_page": 12},
            {"newer_boundary_post_id": 201},
            {"older_boundary_post_id": 201},
            {"post_id": True},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                absence(**values)

    def test_effective_coverage_uses_absence_without_changing_page_ranges(self) -> None:
        page_intervals = [interval(100, 200), interval(202, 300)]

        singleton = absence_to_interval(absence())
        effective = normalize_effective_coverage(page_intervals, [absence()])

        self.assertEqual(
            (singleton.oldest_post_id, singleton.newest_post_id),
            (201, 201),
        )
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in effective],
            [(100, 300)],
        )
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in page_intervals],
            [(100, 200), (202, 300)],
        )


class CoverageRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        schema_path = Path(__file__).resolve().parents[1] / "cloudflare" / "schema.sql"
        self.schema = schema_path.read_text(encoding="utf-8")
        self.connection.executescript(self.schema)
        self.connection.execute(
            """
            INSERT INTO sources (
              source_key, site_name, board_name, board_url, min_upvotes, min_comments
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [SOURCE_KEY, "dcinside", "board", "https://example.com", 4, 15],
        )
        self.connection.commit()
        self.client = SqliteD1Client(self.connection)
        self.repository = CoverageRepository(self.client)
        self.absence_repository = CoverageAbsenceRepository(self.client)

    def tearDown(self) -> None:
        self.connection.close()

    def test_schema_can_be_applied_twice(self) -> None:
        self.connection.executescript(self.schema)
        table = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'coverage_intervals'"
        ).fetchone()
        self.assertEqual(table, ("coverage_intervals",))
        absence_table = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'coverage_absences'"
        ).fetchone()
        self.assertEqual(absence_table, ("coverage_absences",))
        absence_index = self.connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'index' AND name = 'idx_coverage_absences_source_checked'
            """
        ).fetchone()
        self.assertEqual(
            absence_index,
            ("idx_coverage_absences_source_checked",),
        )
        absence_foreign_keys = self.connection.execute(
            "PRAGMA foreign_key_list(coverage_absences)"
        ).fetchall()
        self.assertTrue(
            any(
                row[2:5] == ("sources", "source_key", "source_key")
                for row in absence_foreign_keys
            )
        )

    def test_repository_is_idempotent(self) -> None:
        scanned = interval(100, 200)
        self.repository.record_scanned(scanned)
        self.repository.record_scanned(scanned)
        loaded = self.repository.load(SOURCE_KEY)
        self.assertEqual(len(loaded), 1)
        self.assertEqual((loaded[0].oldest_post_id, loaded[0].newest_post_id), (100, 200))
        self.assertTrue(self.repository.contains(SOURCE_KEY, 150))

    def test_repository_inserts_merged_range_before_deleting_old_rows(self) -> None:
        self.repository.record_scanned(interval(100, 200))
        self.repository.record_scanned(interval(300, 400))
        self.client.operations.clear()

        self.repository.record_scanned(interval(200, 300))

        self.assertEqual(self.client.operations[0], "SELECT")
        self.assertEqual(self.client.operations[1], "INSERT")
        self.assertEqual(self.client.operations[2:], ["DELETE", "DELETE"])
        loaded = self.repository.load(SOURCE_KEY)
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in loaded],
            [(100, 400)],
        )

    def test_repository_keeps_real_gap(self) -> None:
        self.repository.record_scanned(interval(100, 200))
        self.repository.record_scanned(interval(202, 300))
        loaded = self.repository.load(SOURCE_KEY)
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in loaded],
            [(100, 200), (202, 300)],
        )

    def test_repository_can_reuse_cycle_cache_without_reloading_all_ranges(self) -> None:
        self.repository.record_scanned(interval(100, 200))
        self.repository.record_scanned(interval(300, 400))
        cached = self.repository.load(SOURCE_KEY)
        self.client.operations.clear()

        self.repository.record_scanned(
            interval(200, 300),
            existing=cached,
        )

        self.assertEqual(self.client.operations, ["INSERT", "DELETE", "DELETE"])
        loaded = self.repository.load(SOURCE_KEY)
        self.assertEqual(
            [(item.oldest_post_id, item.newest_post_id) for item in loaded],
            [(100, 400)],
        )

    def test_absence_repository_record_is_idempotent_and_refreshes_evidence(self) -> None:
        self.absence_repository.record(absence())
        refreshed = absence(
            newer_page=20,
            older_page=21,
            newer_boundary_post_id=205,
            older_boundary_post_id=199,
            checked_at="2026-07-16T01:00:00+00:00",
        )
        self.absence_repository.record(refreshed)

        loaded = self.absence_repository.load(SOURCE_KEY)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].post_id, 201)
        self.assertEqual((loaded[0].newer_page, loaded[0].older_page), (20, 21))
        self.assertEqual(
            (
                loaded[0].newer_boundary_post_id,
                loaded[0].older_boundary_post_id,
            ),
            (205, 199),
        )
        self.assertEqual(loaded[0].checked_at, "2026-07-16T01:00:00+00:00")
        self.assertEqual(loaded[0].created_at, FIXED_TIME)

    def test_absence_repository_delete_removes_reappeared_id(self) -> None:
        self.absence_repository.record(absence())

        self.absence_repository.delete(SOURCE_KEY, 201)
        self.absence_repository.delete(SOURCE_KEY, 201)

        self.assertEqual(self.absence_repository.load(SOURCE_KEY), [])

    def test_absence_schema_checks_reject_invalid_direct_writes(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute(
                """
                INSERT INTO coverage_absences (
                  source_key, post_id, newer_page, older_page,
                  newer_boundary_post_id, older_boundary_post_id,
                  checked_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [SOURCE_KEY, 201, 10, 12, 202, 200, FIXED_TIME, FIXED_TIME, FIXED_TIME],
            )


if __name__ == "__main__":
    unittest.main()
