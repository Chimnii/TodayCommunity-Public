from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from crawler.d1 import D1Client


@dataclass
class SourceState:
    source_key: str
    head_anchor_history: List[Dict[str, Any]] = field(default_factory=list)
    recovery_mode: bool = False
    recovery_depth_hint: int = 1
    backfill_anchor_post_id: str = ""
    backfill_anchor_created_at: str = ""
    backfill_page_hint: Optional[int] = None
    blocked_until: str = ""
    last_blocked_at: str = ""
    last_block_reason: str = ""
    state_metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def source_state_initialization_statement(
    source_key: str,
    updated_at: Optional[str] = None,
) -> Tuple[str, List[object]]:
    timestamp = updated_at or utc_now()
    return (
        """
        INSERT OR IGNORE INTO source_state (
          source_key,
          updated_at
        )
        SELECT ?, ?
        WHERE EXISTS (
          SELECT 1
          FROM sources
          WHERE source_key = ?
        )
        """,
        [source_key, timestamp, source_key],
    )


def ensure_source_state(client: D1Client, source_key: str, updated_at: Optional[str] = None) -> None:
    sql, params = source_state_initialization_statement(source_key, updated_at)
    client.query(sql, params)


def get_source_state(client: D1Client, source_key: str) -> Optional[SourceState]:
    rows = client.query(
        """
        SELECT
          source_key,
          head_anchor_history,
          recovery_mode,
          recovery_depth_hint,
          backfill_anchor_post_id,
          backfill_anchor_created_at,
          backfill_page_hint,
          blocked_until,
          last_blocked_at,
          last_block_reason,
          state_metadata,
          created_at,
          updated_at
        FROM source_state
        WHERE source_key = ?
        LIMIT 1
        """,
        [source_key],
    )
    return row_to_source_state(rows[0]) if rows else None


def save_source_state(client: D1Client, state: SourceState) -> None:
    payload = normalize_state_payload(state)
    client.query(
        """
        INSERT INTO source_state (
          source_key,
          head_anchor_history,
          recovery_mode,
          recovery_depth_hint,
          backfill_anchor_post_id,
          backfill_anchor_created_at,
          backfill_page_hint,
          blocked_until,
          last_blocked_at,
          last_block_reason,
          state_metadata,
          created_at,
          updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_key) DO UPDATE SET
          head_anchor_history = excluded.head_anchor_history,
          recovery_mode = excluded.recovery_mode,
          recovery_depth_hint = excluded.recovery_depth_hint,
          backfill_anchor_post_id = excluded.backfill_anchor_post_id,
          backfill_anchor_created_at = excluded.backfill_anchor_created_at,
          backfill_page_hint = excluded.backfill_page_hint,
          blocked_until = excluded.blocked_until,
          last_blocked_at = excluded.last_blocked_at,
          last_block_reason = excluded.last_block_reason,
          state_metadata = excluded.state_metadata,
          updated_at = excluded.updated_at
        """,
        [
            payload["source_key"],
            payload["head_anchor_history"],
            payload["recovery_mode"],
            payload["recovery_depth_hint"],
            payload["backfill_anchor_post_id"],
            payload["backfill_anchor_created_at"],
            payload["backfill_page_hint"],
            payload["blocked_until"],
            payload["last_blocked_at"],
            payload["last_block_reason"],
            payload["state_metadata"],
            payload["created_at"],
            payload["updated_at"],
        ],
    )


def normalize_state_payload(state: SourceState) -> Dict[str, Any]:
    current = utc_now()
    payload = asdict(state)
    payload["head_anchor_history"] = json.dumps(state.head_anchor_history, ensure_ascii=False)
    payload["state_metadata"] = json.dumps(state.state_metadata, ensure_ascii=False)
    payload["recovery_mode"] = 1 if state.recovery_mode else 0
    payload["recovery_depth_hint"] = max(1, int(state.recovery_depth_hint or 1))
    payload["backfill_anchor_post_id"] = state.backfill_anchor_post_id or ""
    payload["backfill_anchor_created_at"] = state.backfill_anchor_created_at or ""
    payload["blocked_until"] = state.blocked_until or ""
    payload["last_blocked_at"] = state.last_blocked_at or ""
    payload["last_block_reason"] = state.last_block_reason or ""
    payload["created_at"] = state.created_at or current
    payload["updated_at"] = current
    return payload


def row_to_source_state(row: Dict[str, Any]) -> SourceState:
    return SourceState(
        source_key=str(row.get("source_key", "")),
        head_anchor_history=decode_json_object(row.get("head_anchor_history"), default=[]),
        recovery_mode=bool(row.get("recovery_mode", 0)),
        recovery_depth_hint=max(1, int(row.get("recovery_depth_hint") or 1)),
        backfill_anchor_post_id=str(row.get("backfill_anchor_post_id") or ""),
        backfill_anchor_created_at=str(row.get("backfill_anchor_created_at") or ""),
        backfill_page_hint=parse_optional_int(row.get("backfill_page_hint")),
        blocked_until=str(row.get("blocked_until") or ""),
        last_blocked_at=str(row.get("last_blocked_at") or ""),
        last_block_reason=str(row.get("last_block_reason") or ""),
        state_metadata=decode_json_object(row.get("state_metadata"), default={}),
        created_at=str(row.get("created_at") or ""),
        updated_at=str(row.get("updated_at") or ""),
    )


def decode_json_object(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default

    if isinstance(value, (dict, list)):
        return value

    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def parse_optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(value)
