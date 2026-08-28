from __future__ import annotations

import json
from typing import Dict, Iterable, List, Sequence, Tuple

from crawler.config import get_required_env
from crawler.d1 import D1Client


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
}

REQUIRED_TABLES = tuple(REQUIRED_COLUMNS)


class SchemaValidationError(RuntimeError):
    def __init__(self, report: dict) -> None:
        self.report = report
        errors = report.get("errors") or ["unknown schema validation error"]
        super().__init__("; ".join(str(error) for error in errors))


def inspect_schema(client: D1Client) -> dict:
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
        required_indexes = _required_index_details(
            client,
            index_rows,
            REQUIRED_INDEXES.get(table_name, {}),
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

        for index_name, expected_columns in REQUIRED_INDEXES.get(
            table_name, {}
        ).items():
            actual = required_indexes.get(index_name)
            if actual is None:
                errors.append(
                    f"table {table_name!r} is missing required index "
                    f"{index_name!r} on {_format_key(expected_columns)}"
                )
                continue
            actual_columns = tuple(actual["columns"])
            if actual_columns != expected_columns or actual["partial"] != 0:
                errors.append(
                    f"table {table_name!r} index {index_name!r} must be a "
                    f"non-partial index on {_format_key(expected_columns)}; found "
                    f"{_format_key(actual_columns)} (partial={actual['partial']})"
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
    if required_post_time_columns.issubset(post_columns):
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

    return {
        "valid": not errors,
        "required_tables": list(REQUIRED_TABLES),
        "missing_tables": sorted(set(REQUIRED_TABLES) - present),
        "tables": details,
        "errors": errors,
    }


def validate_schema(client: D1Client) -> dict:
    report = inspect_schema(client)
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
        column_rows = client.query(
            f"PRAGMA index_info({_quote_identifier(index_name)})"
        )
        ordered = sorted(column_rows, key=lambda row: _pragma_int(row.get("seqno")))
        details[index_name] = {
            "columns": tuple(str(row.get("name") or "") for row in ordered),
            "partial": _pragma_int(index_row.get("partial")),
        }
    return details


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
    client = D1Client(
        account_id=get_required_env("TC_CF_ACCOUNT_ID"),
        database_id=get_required_env("TC_CF_DATABASE_ID"),
        api_token=get_required_env("TC_CF_API_TOKEN"),
    )
    try:
        report = validate_schema(client)
    except SchemaValidationError as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2))
        raise SystemExit(f"D1 schema preflight failed: {exc}") from exc
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
