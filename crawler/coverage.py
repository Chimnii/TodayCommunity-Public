from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from crawler.d1 import D1Client


NUMERIC_POST_ID_PATTERN = re.compile(r"^[0-9]+$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def validate_numeric_post_id(value: object) -> int:
    """Return a positive integer post ID, rejecting lossy numeric conversions."""
    if isinstance(value, bool):
        raise ValueError("Post ID must be a positive integer, not a boolean.")

    if isinstance(value, int):
        post_id = value
    elif isinstance(value, str) and NUMERIC_POST_ID_PATTERN.fullmatch(value.strip()):
        post_id = int(value.strip())
    else:
        raise ValueError(f"Post ID must contain decimal digits only: {value!r}")

    if post_id <= 0:
        raise ValueError(f"Post ID must be positive: {value!r}")
    return post_id


@dataclass(frozen=True)
class CoverageInterval:
    """An inclusive post-ID range that was fully checked after the cutoff."""

    source_key: str
    oldest_post_id: int
    newest_post_id: int
    oldest_created_at: str = ""
    newest_created_at: str = ""
    checked_at: str = field(default_factory=utc_now)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        source_key = str(self.source_key).strip()
        if not source_key:
            raise ValueError("Coverage interval requires a non-empty source key.")

        oldest_post_id = validate_numeric_post_id(self.oldest_post_id)
        newest_post_id = validate_numeric_post_id(self.newest_post_id)
        if oldest_post_id > newest_post_id:
            raise ValueError(
                "Coverage interval oldest_post_id must not exceed newest_post_id."
            )

        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "oldest_post_id", oldest_post_id)
        object.__setattr__(self, "newest_post_id", newest_post_id)

    @property
    def key(self) -> Tuple[str, int, int]:
        return (self.source_key, self.oldest_post_id, self.newest_post_id)

    def contains(self, post_id: object) -> bool:
        numeric_post_id = validate_numeric_post_id(post_id)
        return self.oldest_post_id <= numeric_post_id <= self.newest_post_id

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "CoverageInterval":
        return cls(
            source_key=str(row.get("source_key") or ""),
            oldest_post_id=validate_numeric_post_id(row.get("oldest_post_id")),
            newest_post_id=validate_numeric_post_id(row.get("newest_post_id")),
            oldest_created_at=str(row.get("oldest_created_at") or ""),
            newest_created_at=str(row.get("newest_created_at") or ""),
            checked_at=str(row.get("checked_at") or ""),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
        )


@dataclass(frozen=True)
class CoverageAbsence:
    """Evidence that a post ID was absent across two adjacent board pages.

    ``newer_boundary_post_id`` is the oldest ID observed on the newer page;
    ``older_boundary_post_id`` is the newest ID observed on the older page.
    This is absence evidence, not an inferred creation timestamp. If the ID is
    ever observed later, the crawler must delete this evidence.
    """

    source_key: str
    post_id: int
    newer_page: int
    older_page: int
    newer_boundary_post_id: int
    older_boundary_post_id: int
    checked_at: str = field(default_factory=utc_now)
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        source_key = str(self.source_key).strip()
        if not source_key:
            raise ValueError("Coverage absence requires a non-empty source key.")

        post_id = validate_numeric_post_id(self.post_id)
        newer_page = _validate_positive_integer(self.newer_page, "newer_page")
        older_page = _validate_positive_integer(self.older_page, "older_page")
        newer_boundary_post_id = validate_numeric_post_id(
            self.newer_boundary_post_id
        )
        older_boundary_post_id = validate_numeric_post_id(
            self.older_boundary_post_id
        )

        if older_page != newer_page + 1:
            raise ValueError(
                "Coverage absence requires adjacent pages with "
                "older_page = newer_page + 1."
            )
        if not older_boundary_post_id < post_id < newer_boundary_post_id:
            raise ValueError(
                "Coverage absence post_id must lie strictly between its "
                "older and newer boundary post IDs."
            )

        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "post_id", post_id)
        object.__setattr__(self, "newer_page", newer_page)
        object.__setattr__(self, "older_page", older_page)
        object.__setattr__(
            self, "newer_boundary_post_id", newer_boundary_post_id
        )
        object.__setattr__(
            self, "older_boundary_post_id", older_boundary_post_id
        )

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "CoverageAbsence":
        return cls(
            source_key=str(row.get("source_key") or ""),
            post_id=validate_numeric_post_id(row.get("post_id")),
            newer_page=_validate_positive_integer(
                row.get("newer_page"), "newer_page"
            ),
            older_page=_validate_positive_integer(
                row.get("older_page"), "older_page"
            ),
            newer_boundary_post_id=validate_numeric_post_id(
                row.get("newer_boundary_post_id")
            ),
            older_boundary_post_id=validate_numeric_post_id(
                row.get("older_boundary_post_id")
            ),
            checked_at=str(row.get("checked_at") or ""),
            created_at=str(row.get("created_at") or ""),
            updated_at=str(row.get("updated_at") or ""),
        )


@dataclass(frozen=True)
class CoverageMergeResult:
    merged: CoverageInterval
    superseded: Tuple[CoverageInterval, ...]


def intervals_touch(left: CoverageInterval, right: CoverageInterval) -> bool:
    """Return true only for overlap or exact integer adjacency."""
    if left.source_key != right.source_key:
        return False
    return (
        left.oldest_post_id <= right.newest_post_id + 1
        and right.oldest_post_id <= left.newest_post_id + 1
    )


def contains_post_id(intervals: Iterable[CoverageInterval], post_id: object) -> bool:
    numeric_post_id = validate_numeric_post_id(post_id)
    return any(interval.contains(numeric_post_id) for interval in intervals)


def normalize_intervals(
    intervals: Iterable[CoverageInterval],
) -> List[CoverageInterval]:
    """Return a canonical union, without merging across a real numeric gap."""
    grouped: Dict[str, List[CoverageInterval]] = {}
    for interval in intervals:
        grouped.setdefault(interval.source_key, []).append(interval)

    normalized: List[CoverageInterval] = []
    for source_key in sorted(grouped):
        ordered = sorted(
            grouped[source_key],
            key=lambda item: (item.oldest_post_id, item.newest_post_id),
        )
        if not ordered:
            continue

        current = ordered[0]
        for candidate in ordered[1:]:
            if intervals_touch(current, candidate):
                current = _union_intervals((current, candidate))
            else:
                normalized.append(current)
                current = candidate
        normalized.append(current)

    return normalized


def absence_to_interval(absence: CoverageAbsence) -> CoverageInterval:
    """Represent confirmed absence evidence as singleton logical coverage."""
    return CoverageInterval(
        source_key=absence.source_key,
        oldest_post_id=absence.post_id,
        newest_post_id=absence.post_id,
        checked_at=absence.checked_at,
        created_at=absence.created_at,
        updated_at=absence.updated_at,
    )


def normalize_effective_coverage(
    intervals: Iterable[CoverageInterval],
    absences: Iterable[CoverageAbsence],
) -> List[CoverageInterval]:
    """Union page coverage with authoritative missing-ID evidence in memory."""
    return normalize_intervals(
        [*intervals, *(absence_to_interval(absence) for absence in absences)]
    )


def merge_scanned_interval(
    existing: Iterable[CoverageInterval],
    scanned: CoverageInterval,
) -> CoverageMergeResult:
    """Merge only the connected component reached by an actual scanned range."""
    remaining = list(existing)
    for interval in remaining:
        if interval.source_key != scanned.source_key:
            raise ValueError("Cannot merge coverage intervals from different sources.")

    merged = scanned
    superseded: List[CoverageInterval] = []
    while True:
        connected = [item for item in remaining if intervals_touch(item, merged)]
        if not connected:
            break

        connected_keys = {item.key for item in connected}
        remaining = [item for item in remaining if item.key not in connected_keys]
        superseded.extend(connected)
        merged = _union_intervals((merged, *connected))

    return CoverageMergeResult(
        merged=merged,
        superseded=tuple(sorted(superseded, key=lambda item: item.key)),
    )


def load_coverage_intervals(
    client: D1Client,
    source_key: str,
    *,
    normalized: bool = True,
) -> List[CoverageInterval]:
    rows = client.query(
        """
        SELECT
          source_key,
          oldest_post_id,
          newest_post_id,
          oldest_created_at,
          newest_created_at,
          checked_at,
          created_at,
          updated_at
        FROM coverage_intervals
        WHERE source_key = ?
        ORDER BY oldest_post_id ASC, newest_post_id ASC
        """,
        [source_key],
    )
    intervals = [CoverageInterval.from_row(row) for row in rows]
    return normalize_intervals(intervals) if normalized else intervals


class CoverageRepository:
    def __init__(self, client: D1Client) -> None:
        self.client = client

    def load(self, source_key: str) -> List[CoverageInterval]:
        return load_coverage_intervals(self.client, source_key)

    def contains(self, source_key: str, post_id: object) -> bool:
        numeric_post_id = validate_numeric_post_id(post_id)
        rows = self.client.query(
            """
            SELECT 1 AS present
            FROM coverage_intervals
            WHERE source_key = ?
              AND oldest_post_id <= ?
              AND newest_post_id >= ?
            LIMIT 1
            """,
            [source_key, numeric_post_id, numeric_post_id],
        )
        return bool(rows)

    def record_scanned(
        self,
        scanned: CoverageInterval,
        *,
        existing: Optional[Sequence[CoverageInterval]] = None,
    ) -> CoverageInterval:
        known_intervals = (
            list(existing)
            if existing is not None
            else load_coverage_intervals(
                self.client,
                scanned.source_key,
                normalized=False,
            )
        )
        merge_result = merge_scanned_interval(known_intervals, scanned)
        merged = merge_result.merged

        # Insert the complete replacement first. If cleanup later fails, the DB
        # retains redundant true coverage rather than losing checked coverage.
        self._insert(merged)
        for old_interval in merge_result.superseded:
            if old_interval.key == merged.key:
                continue
            self._delete(old_interval)
        return merged

    def _insert(self, interval: CoverageInterval) -> None:
        self.client.query(
            """
            INSERT INTO coverage_intervals (
              source_key,
              oldest_post_id,
              newest_post_id,
              oldest_created_at,
              newest_created_at,
              checked_at,
              created_at,
              updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key, oldest_post_id, newest_post_id) DO UPDATE SET
              oldest_created_at = excluded.oldest_created_at,
              newest_created_at = excluded.newest_created_at,
              checked_at = excluded.checked_at,
              updated_at = excluded.updated_at
            """,
            [
                interval.source_key,
                interval.oldest_post_id,
                interval.newest_post_id,
                interval.oldest_created_at,
                interval.newest_created_at,
                interval.checked_at,
                interval.created_at,
                interval.updated_at,
            ],
        )

    def _delete(self, interval: CoverageInterval) -> None:
        self.client.query(
            """
            DELETE FROM coverage_intervals
            WHERE source_key = ?
              AND oldest_post_id = ?
              AND newest_post_id = ?
            """,
            [interval.source_key, interval.oldest_post_id, interval.newest_post_id],
        )


class CoverageAbsenceRepository:
    def __init__(self, client: D1Client) -> None:
        self.client = client

    def load(self, source_key: str) -> List[CoverageAbsence]:
        rows = self.client.query(
            """
            SELECT
              source_key,
              post_id,
              newer_page,
              older_page,
              newer_boundary_post_id,
              older_boundary_post_id,
              checked_at,
              created_at,
              updated_at
            FROM coverage_absences
            WHERE source_key = ?
            ORDER BY post_id ASC
            """,
            [source_key],
        )
        return [CoverageAbsence.from_row(row) for row in rows]

    def record(self, absence: CoverageAbsence) -> CoverageAbsence:
        self.client.query(
            """
            INSERT INTO coverage_absences (
              source_key,
              post_id,
              newer_page,
              older_page,
              newer_boundary_post_id,
              older_boundary_post_id,
              checked_at,
              created_at,
              updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key, post_id) DO UPDATE SET
              newer_page = excluded.newer_page,
              older_page = excluded.older_page,
              newer_boundary_post_id = excluded.newer_boundary_post_id,
              older_boundary_post_id = excluded.older_boundary_post_id,
              checked_at = excluded.checked_at,
              updated_at = excluded.updated_at
            """,
            [
                absence.source_key,
                absence.post_id,
                absence.newer_page,
                absence.older_page,
                absence.newer_boundary_post_id,
                absence.older_boundary_post_id,
                absence.checked_at,
                absence.created_at,
                absence.updated_at,
            ],
        )
        return absence

    def delete(self, source_key: str, post_id: object) -> None:
        normalized_source_key = str(source_key).strip()
        if not normalized_source_key:
            raise ValueError("Coverage absence deletion requires a source key.")
        numeric_post_id = validate_numeric_post_id(post_id)
        self.client.query(
            """
            DELETE FROM coverage_absences
            WHERE source_key = ? AND post_id = ?
            """,
            [normalized_source_key, numeric_post_id],
        )


def _union_intervals(intervals: Sequence[CoverageInterval]) -> CoverageInterval:
    if not intervals:
        raise ValueError("At least one interval is required for a coverage union.")

    source_key = intervals[0].source_key
    if any(interval.source_key != source_key for interval in intervals):
        raise ValueError("Cannot union coverage intervals from different sources.")

    oldest_post_id = min(interval.oldest_post_id for interval in intervals)
    newest_post_id = max(interval.newest_post_id for interval in intervals)
    return CoverageInterval(
        source_key=source_key,
        oldest_post_id=oldest_post_id,
        newest_post_id=newest_post_id,
        oldest_created_at=_boundary_timestamp(
            intervals,
            boundary_name="oldest_post_id",
            boundary_value=oldest_post_id,
            timestamp_name="oldest_created_at",
        ),
        newest_created_at=_boundary_timestamp(
            intervals,
            boundary_name="newest_post_id",
            boundary_value=newest_post_id,
            timestamp_name="newest_created_at",
        ),
        checked_at=_max_nonempty(interval.checked_at for interval in intervals),
        created_at=_min_nonempty(interval.created_at for interval in intervals),
        updated_at=_max_nonempty(interval.updated_at for interval in intervals),
    )


def _boundary_timestamp(
    intervals: Sequence[CoverageInterval],
    *,
    boundary_name: str,
    boundary_value: int,
    timestamp_name: str,
) -> str:
    values = (
        str(getattr(interval, timestamp_name) or "")
        for interval in intervals
        if getattr(interval, boundary_name) == boundary_value
    )
    return _max_nonempty(values)


def _min_nonempty(values: Iterable[str]) -> str:
    populated = [value for value in values if value]
    return min(populated) if populated else ""


def _max_nonempty(values: Iterable[str]) -> str:
    populated = [value for value in values if value]
    return max(populated) if populated else ""


def _validate_positive_integer(value: object, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer, not a boolean.")
    if isinstance(value, int):
        numeric_value = value
    elif isinstance(value, str) and NUMERIC_POST_ID_PATTERN.fullmatch(value.strip()):
        numeric_value = int(value.strip())
    else:
        raise ValueError(f"{name} must be a positive integer: {value!r}")
    if numeric_value <= 0:
        raise ValueError(f"{name} must be positive: {value!r}")
    return numeric_value
