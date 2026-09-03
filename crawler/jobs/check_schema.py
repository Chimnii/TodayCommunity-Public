from __future__ import annotations

import argparse
import json
import re
from typing import Dict, Iterable, List, Sequence, Tuple

from crawler.config import get_required_env
from crawler.d1 import D1Client, d1_usage_label, d1_usage_summary


REQUIRED_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "archives": (
        "archive_key",
        "display_name",
        "description",
        "display_order",
        "is_public",
        "created_at",
        "updated_at",
    ),
    "sources": (
        "source_key",
        "archive_key",
        "site_name",
        "board_name",
        "board_url",
        "min_upvotes",
        "min_comments",
        "created_at",
        "updated_at",
    ),
    "posts": (
        "id",
        "source_key",
        "archive_key",
        "canonical_post_key",
        "external_post_id",
        "post_url",
        "subject",
        "title",
        "created_at",
        "created_at_raw",
        "created_at_basis",
        "created_at_precision",
        "upvotes",
        "comments",
        "fetched_at",
        "first_seen_at",
        "last_seen_at",
        "qualifies_by",
        "status",
    ),
    "archive_stats": (
        "archive_key",
        "active_post_count",
        "latest_seen_at",
        "subject_options_json",
        "stats_version",
        "mutation_token",
        "updated_at",
    ),
    "archive_subject_stats": (
        "archive_key",
        "subject",
        "active_post_count",
        "updated_at",
    ),
    "crawl_runs": (
        "id",
        "source_key",
        "run_type",
        "status",
        "scanned_pages",
        "scanned_posts",
        "matched_posts",
        "started_at",
        "finished_at",
        "error_message",
    ),
    "source_state": (
        "source_key",
        "head_anchor_history",
        "recovery_mode",
        "recovery_depth_hint",
        "backfill_anchor_post_id",
        "backfill_anchor_created_at",
        "backfill_page_hint",
        "blocked_until",
        "last_blocked_at",
        "last_block_reason",
        "state_metadata",
        "created_at",
        "updated_at",
    ),
    "coverage_intervals": (
        "source_key",
        "oldest_post_id",
        "newest_post_id",
        "oldest_created_at",
        "newest_created_at",
        "checked_at",
        "created_at",
        "updated_at",
    ),
    "coverage_absences": (
        "source_key",
        "post_id",
        "newer_page",
        "older_page",
        "newer_boundary_post_id",
        "older_boundary_post_id",
        "checked_at",
        "created_at",
        "updated_at",
    ),
}

REQUIRED_PRIMARY_KEYS: Dict[str, Tuple[str, ...]] = {
    "archives": ("archive_key",),
    "sources": ("source_key",),
    "posts": ("id",),
    "archive_stats": ("archive_key",),
    "archive_subject_stats": ("archive_key", "subject"),
    "crawl_runs": ("id",),
    "source_state": ("source_key",),
    "coverage_intervals": (
        "source_key",
        "oldest_post_id",
        "newest_post_id",
    ),
    "coverage_absences": ("source_key", "post_id"),
}

REQUIRED_UNIQUE_KEYS: Dict[str, Tuple[Tuple[str, ...], ...]] = {
    "posts": (
        ("source_key", "external_post_id"),
        ("archive_key", "canonical_post_key"),
    ),
}

REQUIRED_INDEXES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "crawl_runs": {
        "idx_crawl_runs_source_status_id": ("source_key", "status", "id"),
        "idx_crawl_runs_source_id": ("source_key", "id"),
    },
}

REQUIRED_PARTIAL_INDEXES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "posts": {
        "idx_posts_active_created": ("created_at", "id"),
        "idx_posts_active_upvotes": ("upvotes", "created_at", "id"),
        "idx_posts_active_comments": ("comments", "created_at", "id"),
    },
}

REQUIRED_INDEX_DIRECTIONS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "crawl_runs": {
        "idx_crawl_runs_source_status_id": ("ASC", "ASC", "DESC"),
        "idx_crawl_runs_source_id": ("ASC", "DESC"),
    },
    "posts": {
        "idx_posts_active_created": ("DESC", "DESC"),
        "idx_posts_active_upvotes": ("DESC", "DESC", "DESC"),
        "idx_posts_active_comments": ("DESC", "DESC", "DESC"),
    },
}

IndexVariant = Tuple[Tuple[str, ...], Tuple[str, ...], int]

REQUIRED_INDEX_VARIANTS: Dict[
    str,
    Dict[str, Tuple[IndexVariant, ...]],
] = {
    "posts": {
        "idx_posts_archive_created_at": (
            (("archive_key", "created_at"), ("ASC", "DESC"), 0),
            (
                ("archive_key", "created_at", "id"),
                ("ASC", "DESC", "DESC"),
                1,
            ),
        ),
        "idx_posts_archive_upvotes": (
            (("archive_key", "upvotes"), ("ASC", "DESC"), 0),
            (
                ("archive_key", "upvotes", "created_at", "id"),
                ("ASC", "DESC", "DESC", "DESC"),
                1,
            ),
        ),
        "idx_posts_archive_comments": (
            (("archive_key", "comments"), ("ASC", "DESC"), 0),
            (
                ("archive_key", "comments", "created_at", "id"),
                ("ASC", "DESC", "DESC", "DESC"),
                1,
            ),
        ),
    },
}

REQUIRED_PARTIAL_INDEX_PREDICATE = "status='active'"

REQUIRED_COLUMN_PROPERTIES = {
    "sources": {
        "archive_key": {
            "type": "TEXT",
            "notnull": 1,
            "default": "'dcinside-singularity'",
        },
    },
    "posts": {
        "archive_key": {
            "type": "TEXT",
            "notnull": 1,
            "default": "'dcinside-singularity'",
        },
        "subject": {
            "type": "TEXT",
            "notnull": 1,
            "default": "''",
        },
        "created_at_basis": {
            "type": "TEXT",
            "notnull": 1,
            "default": "'source'",
        },
        "created_at_precision": {
            "type": "TEXT",
            "notnull": 1,
            "default": "'second'",
        },
    },
    "archive_stats": {
        "active_post_count": {
            "type": "INTEGER",
            "notnull": 1,
            "default": "0",
        },
        "latest_seen_at": {
            "type": "TEXT",
            "notnull": 1,
            "default": "''",
        },
        "subject_options_json": {
            "type": "TEXT",
            "notnull": 1,
            "default": "'[]'",
        },
        "stats_version": {
            "type": "INTEGER",
            "notnull": 1,
            "default": "0",
        },
        "mutation_token": {
            "type": "TEXT",
            "notnull": 1,
            "default": "''",
        },
    },
    "archive_subject_stats": {
        "active_post_count": {
            "type": "INTEGER",
            "notnull": 1,
            "default": "0",
        },
    },
}

REQUIRED_TABLES = tuple(REQUIRED_COLUMNS)


class SchemaValidationError(RuntimeError):
    def __init__(self, report: dict) -> None:
        self.report = report
        errors = report.get("errors") or ["unknown schema validation error"]
        super().__init__("; ".join(str(error) for error in errors))


def inspect_schema(client: D1Client, *, deep_data_audit: bool = False) -> dict:
    """Inspect the runtime-critical D1 schema without modifying it."""

    table_rows = client.query("PRAGMA table_list")
    present = {
        str(row.get("name") or "")
        for row in table_rows
        if str(row.get("type") or "table") == "table"
    }
    errors: List[str] = []
    details = {}

    for table_name in REQUIRED_TABLES:
        if table_name not in present:
            errors.append(f"missing required table: {table_name}")
            continue

        columns = _table_columns(client, table_name)
        column_names = tuple(str(row.get("name") or "") for row in columns)
        missing_columns = sorted(set(REQUIRED_COLUMNS[table_name]) - set(column_names))
        primary_key = _primary_key(columns)
        index_rows = client.query(
            f"PRAGMA index_list({_quote_identifier(table_name)})"
        )
        unique_keys = _unique_keys(client, index_rows)
        fixed_index_columns = {
            **REQUIRED_INDEXES.get(table_name, {}),
            **REQUIRED_PARTIAL_INDEXES.get(table_name, {}),
        }
        index_variants = REQUIRED_INDEX_VARIANTS.get(table_name, {})
        required_index_columns = {
            **fixed_index_columns,
            **{
                index_name: variants[-1][0]
                for index_name, variants in index_variants.items()
            },
        }
        required_indexes = _required_index_details(
            client,
            index_rows,
            required_index_columns,
        )

        details[table_name] = {
            "columns": list(column_names),
            "primary_key": list(primary_key),
            "unique_keys": [list(key) for key in unique_keys],
            "required_indexes": {
                name: list(index["columns"])
                for name, index in required_indexes.items()
            },
        }

        if missing_columns:
            errors.append(
                f"table {table_name!r} is missing columns: {', '.join(missing_columns)}"
            )

        columns_by_name = {
            str(row.get("name") or ""): row
            for row in columns
        }
        for column_name, expected in REQUIRED_COLUMN_PROPERTIES.get(
            table_name, {}
        ).items():
            column = columns_by_name.get(column_name)
            if column is None:
                continue
            actual = {
                "type": str(column.get("type") or "").upper(),
                "notnull": _pragma_int(column.get("notnull")),
                "default": (
                    None
                    if column.get("dflt_value") is None
                    else str(column.get("dflt_value"))
                ),
            }
            if actual != expected:
                errors.append(
                    f"table {table_name!r} column {column_name!r} must be "
                    f"{expected}; found {actual}"
                )

        expected_primary_key = REQUIRED_PRIMARY_KEYS[table_name]
        if primary_key != expected_primary_key:
            errors.append(
                f"table {table_name!r} must have PRIMARY KEY "
                f"{_format_key(expected_primary_key)}; found {_format_key(primary_key)}"
            )

        for required_key in REQUIRED_UNIQUE_KEYS.get(table_name, ()):
            if required_key not in unique_keys:
                found = ", ".join(_format_key(key) for key in unique_keys) or "none"
                errors.append(
                    f"table {table_name!r} must have UNIQUE {_format_key(required_key)}; "
                    f"found {found}"
                )

        for index_name, expected_columns in fixed_index_columns.items():
            actual = required_indexes.get(index_name)
            expected_partial = int(
                index_name in REQUIRED_PARTIAL_INDEXES.get(table_name, {})
            )
            if actual is None:
                errors.append(
                    f"table {table_name!r} is missing required index "
                    f"{index_name!r} on {_format_key(expected_columns)}"
                )
                continue
            actual_columns = tuple(actual["columns"])
            if (
                actual_columns != expected_columns
                or actual["partial"] != expected_partial
            ):
                index_kind = "partial" if expected_partial else "non-partial"
                errors.append(
                    f"table {table_name!r} index {index_name!r} must be a "
                    f"{index_kind} index on {_format_key(expected_columns)}; found "
                    f"{_format_key(actual_columns)} (partial={actual['partial']})"
                )
                continue
            expected_directions = REQUIRED_INDEX_DIRECTIONS[table_name][index_name]
            actual_directions = tuple(actual["directions"])
            if actual_directions != expected_directions:
                errors.append(
                    f"table {table_name!r} index {index_name!r} must order "
                    f"{_format_index_order(expected_columns, expected_directions)}; "
                    f"found {_format_index_order(actual_columns, actual_directions)}"
                )
                continue
            actual_collations = tuple(actual["collations"])
            expected_collations = ("BINARY",) * len(expected_columns)
            if actual_collations != expected_collations:
                errors.append(
                    f"table {table_name!r} index {index_name!r} must use BINARY "
                    "collation for every key column; found "
                    f"{_format_key(actual_collations)}"
                )
                continue
            if (
                expected_partial
                and actual["predicate"] != REQUIRED_PARTIAL_INDEX_PREDICATE
            ):
                errors.append(
                    f"table {table_name!r} index {index_name!r} must use "
                    "WHERE status = 'active'; found "
                    f"{_format_index_predicate(actual['predicate'])}"
                )

        for index_name, accepted_variants in index_variants.items():
            actual = required_indexes.get(index_name)
            accepted = ", or ".join(
                f"{'partial' if partial else 'non-partial'} index on "
                f"{_format_key(columns)}"
                for columns, _, partial in accepted_variants
            )
            if actual is None:
                errors.append(
                    f"table {table_name!r} is missing required index "
                    f"{index_name!r}; accepted variants: {accepted}"
                )
                continue
            actual_shape = (
                tuple(actual["columns"]),
                actual["partial"],
            )
            matched_variant = next(
                (
                    variant
                    for variant in accepted_variants
                    if (variant[0], variant[2]) == actual_shape
                ),
                None,
            )
            if matched_variant is None:
                errors.append(
                    f"table {table_name!r} index {index_name!r} must be a "
                    f"{accepted}; found {_format_key(actual_shape[0])} "
                    f"(partial={actual_shape[1]})"
                )
                continue
            expected_directions = matched_variant[1]
            actual_directions = tuple(actual["directions"])
            if actual_directions != expected_directions:
                errors.append(
                    f"table {table_name!r} index {index_name!r} must order "
                    f"{_format_index_order(actual_shape[0], expected_directions)}; "
                    f"found {_format_index_order(actual_shape[0], actual_directions)}"
                )
                continue
            actual_collations = tuple(actual["collations"])
            expected_collations = ("BINARY",) * len(actual_shape[0])
            if actual_collations != expected_collations:
                errors.append(
                    f"table {table_name!r} index {index_name!r} must use BINARY "
                    "collation for every key column; found "
                    f"{_format_key(actual_collations)}"
                )
                continue
            if (
                actual_shape[1]
                and actual["predicate"] != REQUIRED_PARTIAL_INDEX_PREDICATE
            ):
                errors.append(
                    f"table {table_name!r} index {index_name!r} must use "
                    "WHERE status = 'active'; found "
                    f"{_format_index_predicate(actual['predicate'])}"
                )

    post_columns = set(details.get("posts", {}).get("columns", []))
    required_post_time_columns = {
        "created_at",
        "fetched_at",
        "first_seen_at",
        "last_seen_at",
        "created_at_basis",
        "created_at_precision",
    }
    if deep_data_audit and required_post_time_columns.issubset(post_columns):
        time_audit = client.query(
            """
            SELECT
              SUM(CASE WHEN NOT (
                length(created_at) = 20
                AND created_at IS strftime('%Y-%m-%dT%H:%M:%SZ', created_at)
              ) THEN 1 ELSE 0 END) AS invalid_created_at,
              SUM(CASE WHEN NOT (
                length(fetched_at) = 20
                AND fetched_at IS strftime('%Y-%m-%dT%H:%M:%SZ', fetched_at)
              ) THEN 1 ELSE 0 END) AS invalid_fetched_at,
              SUM(CASE WHEN NOT (
                length(first_seen_at) = 20
                AND first_seen_at IS strftime('%Y-%m-%dT%H:%M:%SZ', first_seen_at)
              ) THEN 1 ELSE 0 END) AS invalid_first_seen_at,
              SUM(CASE WHEN NOT (
                length(last_seen_at) = 20
                AND last_seen_at IS strftime('%Y-%m-%dT%H:%M:%SZ', last_seen_at)
              ) THEN 1 ELSE 0 END) AS invalid_last_seen_at,
              SUM(CASE WHEN created_at_basis NOT IN ('source', 'first_seen')
                THEN 1 ELSE 0 END) AS invalid_basis,
              SUM(CASE WHEN created_at_precision NOT IN ('second', 'minute', 'date')
                THEN 1 ELSE 0 END) AS invalid_precision
            FROM posts
            """
        )
        audit = time_audit[0] if time_audit else {}
        details["posts"]["timestamp_audit"] = audit
        invalid_total = sum(_pragma_int(value) for value in audit.values())
        if invalid_total:
            errors.append(
                "table 'posts' contains non-canonical timestamps or invalid "
                f"timestamp metadata: {audit}"
            )

        stats_audit_rows = client.query(
            """
            WITH actual_archive AS (
              SELECT archive_key, count(*) AS active_post_count
              FROM posts
              WHERE status = 'active'
              GROUP BY archive_key
            ),
            actual_subject AS (
              SELECT archive_key, trim(subject) AS subject,
                     count(*) AS active_post_count
              FROM posts
              WHERE status = 'active' AND trim(subject) <> ''
              GROUP BY archive_key, trim(subject)
            ),
            archive_mismatches AS (
              SELECT archive.archive_key
              FROM archives AS archive
              LEFT JOIN archive_stats AS stored
                ON stored.archive_key = archive.archive_key
              LEFT JOIN actual_archive AS actual
                ON actual.archive_key = archive.archive_key
              WHERE stored.archive_key IS NULL
                 OR stored.active_post_count
                    <> coalesce(actual.active_post_count, 0)
              UNION ALL
              SELECT stored.archive_key
              FROM archive_stats AS stored
              LEFT JOIN archives AS archive
                ON archive.archive_key = stored.archive_key
              WHERE archive.archive_key IS NULL
            ),
            subject_mismatches AS (
              SELECT actual.archive_key, actual.subject
              FROM actual_subject AS actual
              LEFT JOIN archive_subject_stats AS stored
                ON stored.archive_key = actual.archive_key
               AND stored.subject = actual.subject
              WHERE stored.archive_key IS NULL
                 OR stored.active_post_count <> actual.active_post_count
              UNION ALL
              SELECT stored.archive_key, stored.subject
              FROM archive_subject_stats AS stored
              LEFT JOIN actual_subject AS actual
                ON actual.archive_key = stored.archive_key
               AND actual.subject = stored.subject
              WHERE actual.archive_key IS NULL
            ),
            expected_options AS (
              SELECT archive.archive_key,
                     coalesce((
                       SELECT json_group_array(subject)
                       FROM (
                         SELECT subject
                         FROM actual_subject
                         WHERE archive_key = archive.archive_key
                           AND length(subject) <= 100
                         ORDER BY subject COLLATE NOCASE ASC, subject ASC
                         LIMIT 100
                       )
                     ), '[]') AS subject_options_json
              FROM archives AS archive
            )
            SELECT
              (SELECT count(*) FROM archive_mismatches)
                AS archive_count_mismatches,
              (SELECT count(*) FROM subject_mismatches)
                AS subject_count_mismatches,
              (
                SELECT count(*)
                FROM expected_options AS expected
                LEFT JOIN archive_stats AS stored
                  ON stored.archive_key = expected.archive_key
                WHERE stored.archive_key IS NULL
                   OR json(stored.subject_options_json)
                      IS NOT json(expected.subject_options_json)
              ) AS subject_options_mismatches
            """
        )
        stats_audit = stats_audit_rows[0] if stats_audit_rows else {}
        details["archive_stats"]["data_audit"] = stats_audit
        stats_mismatch_total = sum(
            _pragma_int(value) for value in stats_audit.values()
        )
        if stats_mismatch_total:
            errors.append(
                "archive statistics do not match active posts; rerun "
                f"cloudflare/migrations/013_archive_stats.sql: {stats_audit}"
            )

    return {
        "valid": not errors,
        "required_tables": list(REQUIRED_TABLES),
        "missing_tables": sorted(set(REQUIRED_TABLES) - present),
        "tables": details,
        "errors": errors,
    }


def validate_schema(client: D1Client, *, deep_data_audit: bool = False) -> dict:
    report = inspect_schema(client, deep_data_audit=deep_data_audit)
    if not report["valid"]:
        raise SchemaValidationError(report)
    return report


def _table_columns(client: D1Client, table_name: str) -> List[dict]:
    return client.query(f"PRAGMA table_info({_quote_identifier(table_name)})")


def _primary_key(columns: Sequence[dict]) -> Tuple[str, ...]:
    primary_columns = [
        (_pragma_int(row.get("pk")), str(row.get("name") or ""))
        for row in columns
        if _pragma_int(row.get("pk")) > 0
    ]
    return tuple(name for _, name in sorted(primary_columns))


def _unique_keys(
    client: D1Client,
    index_rows: Sequence[dict],
) -> Tuple[Tuple[str, ...], ...]:
    keys = []
    for index_row in index_rows:
        if (
            _pragma_int(index_row.get("unique")) != 1
            or _pragma_int(index_row.get("partial")) != 0
        ):
            continue
        index_name = str(index_row.get("name") or "")
        if not index_name:
            continue
        column_rows = client.query(
            f"PRAGMA index_info({_quote_identifier(index_name)})"
        )
        ordered = sorted(column_rows, key=lambda row: _pragma_int(row.get("seqno")))
        key = tuple(str(row.get("name") or "") for row in ordered)
        if key:
            keys.append(key)
    return tuple(sorted(set(keys)))


def _required_index_details(
    client: D1Client,
    index_rows: Sequence[dict],
    required: Dict[str, Tuple[str, ...]],
) -> Dict[str, dict]:
    by_name = {
        str(row.get("name") or ""): row
        for row in index_rows
        if str(row.get("name") or "")
    }
    details = {}
    for index_name in required:
        index_row = by_name.get(index_name)
        if index_row is None:
            continue
        index_column_rows = client.query(
            f"PRAGMA index_xinfo({_quote_identifier(index_name)})"
        )
        definition_rows = client.query(
            "SELECT sql FROM sqlite_schema WHERE type = 'index' AND name = ?",
            [index_name],
        )
        definition_sql = (
            str(definition_rows[0].get("sql") or "")
            if definition_rows
            else ""
        )
        key_rows = [
            row
            for row in index_column_rows
            if _pragma_int(row.get("key")) == 1
        ]
        ordered = sorted(key_rows, key=lambda row: _pragma_int(row.get("seqno")))
        details[index_name] = {
            "columns": tuple(str(row.get("name") or "") for row in ordered),
            "directions": tuple(
                "DESC" if _pragma_int(row.get("desc")) else "ASC"
                for row in ordered
            ),
            "collations": tuple(
                str(row.get("coll") or "").upper()
                for row in ordered
            ),
            "partial": _pragma_int(index_row.get("partial")),
            "predicate": _normalized_index_predicate(definition_sql),
        }
    return details


def _normalized_index_predicate(definition_sql: str) -> str:
    match = re.search(r"\bWHERE\b(?P<predicate>.+)$", definition_sql, re.I | re.S)
    if match is None:
        return ""

    predicate = match.group("predicate").strip().rstrip(";").strip()
    normalized = []
    in_string = False
    index = 0
    while index < len(predicate):
        character = predicate[index]
        if character == "'":
            normalized.append(character)
            if in_string and index + 1 < len(predicate) and predicate[index + 1] == "'":
                normalized.append("'")
                index += 2
                continue
            in_string = not in_string
        elif not in_string and character.isspace():
            index += 1
            continue
        elif in_string:
            normalized.append(character)
        else:
            normalized.append(character.casefold())
        index += 1
    return "".join(normalized)


def _format_index_predicate(predicate: object) -> str:
    normalized = str(predicate or "")
    return f"WHERE {normalized}" if normalized else "no WHERE predicate"


def _format_index_order(
    columns: Sequence[str],
    directions: Sequence[str],
) -> str:
    return _format_key(
        f"{column} {direction}"
        for column, direction in zip(columns, directions)
    )


def _pragma_int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _format_key(columns: Iterable[str]) -> str:
    return "(" + ", ".join(columns) + ")"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the crawler D1 schema.")
    parser.add_argument(
        "--deep-data-audit",
        action="store_true",
        help="Scan post rows for canonical timestamp data after migrations.",
    )
    args = parser.parse_args()
    client = D1Client(
        account_id=get_required_env("TC_CF_ACCOUNT_ID"),
        database_id=get_required_env("TC_CF_DATABASE_ID"),
        api_token=get_required_env("TC_CF_API_TOKEN"),
    )
    try:
        with d1_usage_label(client, "schema"):
            report = validate_schema(client, deep_data_audit=args.deep_data_audit)
    except SchemaValidationError as exc:
        usage = d1_usage_summary(client)
        if usage is not None:
            exc.report["d1_usage"] = usage
        print(json.dumps(exc.report, ensure_ascii=False, indent=2))
        raise SystemExit(f"D1 schema preflight failed: {exc}") from exc
    usage = d1_usage_summary(client)
    if usage is not None:
        report["d1_usage"] = usage
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
