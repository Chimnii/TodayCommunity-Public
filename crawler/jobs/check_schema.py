from __future__ import annotations

import json
from typing import Dict, Iterable, List, Sequence, Tuple

from crawler.config import get_required_env
from crawler.d1 import D1Client


REQUIRED_COLUMNS: Dict[str, Tuple[str, ...]] = {
    "sources": (
        "source_key",
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
        "external_post_id",
        "post_url",
        "subject",
        "title",
        "created_at",
        "created_at_raw",
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
    "posts": (("source_key", "external_post_id"),),
}

REQUIRED_COLUMN_PROPERTIES = {
    "posts": {
        "subject": {
            "type": "TEXT",
            "notnull": 1,
            "default": "''",
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
        unique_keys = _unique_keys(client, table_name)

        details[table_name] = {
            "columns": list(column_names),
            "primary_key": list(primary_key),
            "unique_keys": [list(key) for key in unique_keys],
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


def _unique_keys(client: D1Client, table_name: str) -> Tuple[Tuple[str, ...], ...]:
    keys = []
    index_rows = client.query(f"PRAGMA index_list({_quote_identifier(table_name)})")
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
