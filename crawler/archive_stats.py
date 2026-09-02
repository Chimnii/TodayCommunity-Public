from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence


ARCHIVE_STATS_COLUMNS = (
    "archive_key",
    "active_post_count",
    "latest_seen_at",
    "subject_options_json",
    "stats_version",
    "mutation_token",
    "updated_at",
)
ARCHIVE_SUBJECT_STATS_COLUMNS = (
    "archive_key",
    "subject",
    "active_post_count",
    "updated_at",
)
MAX_SUBJECT_OPTIONS = 100


@dataclass(frozen=True)
class PostStatsTransition:
    old_status: Optional[str]
    old_subject: str
    new_status: Optional[str]
    new_subject: str


def normalized_subject(value: object) -> str:
    return str(value or "").strip()


def active_status(value: object) -> bool:
    return str(value or "") == "active"


def stats_deltas(
    transitions: Iterable[PostStatsTransition],
) -> tuple[int, Counter[str]]:
    active_delta = 0
    subject_deltas: Counter[str] = Counter()
    for transition in transitions:
        old_active = active_status(transition.old_status)
        new_active = active_status(transition.new_status)
        old_subject = normalized_subject(transition.old_subject)
        new_subject = normalized_subject(transition.new_subject)

        active_delta += int(new_active) - int(old_active)
        if old_active and old_subject:
            subject_deltas[old_subject] -= 1
        if new_active and new_subject:
            subject_deltas[new_subject] += 1

    return active_delta, Counter(
        {subject: delta for subject, delta in subject_deltas.items() if delta}
    )


def load_subject_counts(
    client: object,
    archive_key: str,
    subjects: Iterable[str],
) -> dict[str, int]:
    normalized = tuple(
        dict.fromkeys(
            subject
            for subject in (normalized_subject(value) for value in subjects)
            if subject
        )
    )
    if not normalized:
        return {}
    placeholders = ", ".join("?" for _ in normalized)
    rows = client.query(
        f"""
        SELECT subject, active_post_count
        FROM archive_subject_stats
        WHERE archive_key = ?
          AND subject IN ({placeholders})
        """,
        [archive_key, *normalized],
    )
    return {
        normalized_subject(row.get("subject")): int(
            row.get("active_post_count") or 0
        )
        for row in rows
    }


def stats_mutation_statements(
    archive_key: str,
    transitions: Iterable[PostStatsTransition],
    observed_at: str,
    *,
    latest_seen_at: str = "",
    previous_subject_counts: Optional[Mapping[str, int]] = None,
    bump_version: bool = True,
    guard_sql: str = "",
    guard_params: Sequence[object] = (),
    ensure_archive_row: bool = True,
    mutation_token: str = "",
    require_previous_change: bool = False,
) -> list[tuple[str, Sequence[object]]]:
    transition_list = list(transitions)
    active_delta, subject_deltas = stats_deltas(transition_list)
    latest = str(latest_seen_at or "")
    statements: list[tuple[str, Sequence[object]]] = []
    guard = str(guard_sql or "").strip()
    token = str(mutation_token or "").strip()
    if require_previous_change and not token:
        raise ValueError("require_previous_change requires a mutation_token")

    archive_guard = guard
    if require_previous_change:
        archive_guard = (
            f"changes() = 1 AND ({archive_guard})"
            if archive_guard
            else "changes() = 1"
        )
    downstream_guard = guard
    downstream_guard_params: tuple[object, ...] = tuple(guard_params)
    if token:
        token_guard = """
          EXISTS (
            SELECT 1
            FROM archive_stats AS claimed_archive_stats
            WHERE claimed_archive_stats.archive_key = ?
              AND claimed_archive_stats.mutation_token = ?
          )
        """
        downstream_guard = (
            f"({downstream_guard}) AND ({token_guard})"
            if downstream_guard
            else token_guard
        )
        downstream_guard_params = (
            *downstream_guard_params,
            archive_key,
            token,
        )

    if bump_version or active_delta or subject_deltas:
        if ensure_archive_row:
            statements.append(
                (
                    """
                    INSERT OR IGNORE INTO archive_stats (
                      archive_key, active_post_count, latest_seen_at,
                      subject_options_json, stats_version, updated_at
                    ) VALUES (?, 0, '', '[]', 0, ?)
                    """,
                    (archive_key, observed_at),
                )
            )
        mutation_assignment = ", mutation_token = ?" if token else ""
        statements.append(
            (
                f"""
                UPDATE archive_stats
                SET active_post_count = active_post_count + ?,
                    latest_seen_at = max(latest_seen_at, ?),
                    stats_version = stats_version + 1,
                    updated_at = ?
                    {mutation_assignment}
                WHERE archive_key = ?
                  {f"AND ({archive_guard})" if archive_guard else ""}
                """,
                (
                    active_delta,
                    latest,
                    observed_at,
                    *((token,) if token else ()),
                    archive_key,
                    *guard_params,
                ),
            )
        )

    positive = {
        subject: delta for subject, delta in subject_deltas.items() if delta > 0
    }
    negative = {
        subject: delta for subject, delta in subject_deltas.items() if delta < 0
    }
    if positive and not downstream_guard:
        values = ", ".join("(?, ?, ?, ?)" for _ in positive)
        params: list[object] = []
        for subject, delta in positive.items():
            params.extend((archive_key, subject, delta, observed_at))
        statements.append(
            (
                f"""
                INSERT INTO archive_subject_stats (
                  archive_key, subject, active_post_count, updated_at
                ) VALUES {values}
                ON CONFLICT(archive_key, subject) DO UPDATE SET
                  active_post_count = archive_subject_stats.active_post_count
                    + excluded.active_post_count,
                  updated_at = excluded.updated_at
                """,
                params,
            )
        )
    elif positive:
        for subject, delta in positive.items():
            statements.append(
                (
                    f"""
                    INSERT INTO archive_subject_stats (
                      archive_key, subject, active_post_count, updated_at
                    )
                    SELECT ?, ?, ?, ?
                    WHERE ({downstream_guard})
                    ON CONFLICT(archive_key, subject) DO UPDATE SET
                      active_post_count = archive_subject_stats.active_post_count
                        + excluded.active_post_count,
                      updated_at = excluded.updated_at
                    """,
                    (
                        archive_key,
                        subject,
                        delta,
                        observed_at,
                        *downstream_guard_params,
                    ),
                )
            )
    if negative:
        cases = " ".join("WHEN ? THEN ?" for _ in negative)
        placeholders = ", ".join("?" for _ in negative)
        params = []
        for subject, delta in negative.items():
            params.extend((subject, delta))
        params.extend((observed_at, archive_key, *negative.keys()))
        statements.append(
            (
                f"""
                UPDATE archive_subject_stats
                SET active_post_count = active_post_count
                      + CASE subject {cases} ELSE 0 END,
                    updated_at = ?
                WHERE archive_key = ?
                  AND subject IN ({placeholders})
                  {f"AND ({downstream_guard})" if downstream_guard else ""}
                """,
                [*params, *downstream_guard_params],
            )
        )
        statements.append(
            (
                f"""
                DELETE FROM archive_subject_stats
                WHERE archive_key = ?
                  AND subject IN ({placeholders})
                  AND active_post_count = 0
                  {f"AND ({downstream_guard})" if downstream_guard else ""}
                """,
                (archive_key, *negative.keys(), *downstream_guard_params),
            )
        )

    previous = previous_subject_counts or {}
    membership_changed = any(
        (int(previous.get(subject, 0)) == 0)
        != (int(previous.get(subject, 0)) + delta == 0)
        for subject, delta in subject_deltas.items()
    )
    # Guarded game-news writes can race with owner visibility mutations. In
    # that path, rebuild after every subject delta inside the same D1 batch so
    # a crossing decision made before the batch cannot leave stale options.
    refresh_subject_options = membership_changed or bool(
        downstream_guard and subject_deltas
    )
    if refresh_subject_options:
        statements.append(
            (
                f"""
                UPDATE archive_stats
                SET subject_options_json = coalesce((
                  SELECT json_group_array(subject)
                  FROM (
                    SELECT subject
                    FROM archive_subject_stats
                    WHERE archive_key = ?
                      AND active_post_count > 0
                      AND length(subject) <= 100
                    ORDER BY subject COLLATE NOCASE ASC, subject ASC
                    LIMIT {MAX_SUBJECT_OPTIONS}
                  )
                ), '[]')
                WHERE archive_key = ?
                  {f"AND ({downstream_guard})" if downstream_guard else ""}
                """,
                (archive_key, archive_key, *downstream_guard_params),
            )
        )

    return statements
