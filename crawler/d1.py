from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Tuple
from urllib import error, request


D1Statement = Tuple[str, Optional[Iterable[object]]]
DEFAULT_D1_USAGE_LABEL = "unlabeled"
_D1_USAGE_LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass
class _D1UsageBucket:
    statement_count: int = 0
    rows_read: int = 0
    rows_written: int = 0
    incomplete_meta_count: int = 0
    failed_request_count: int = 0

    def as_tuple(self) -> tuple[int, int, int, int, int]:
        return (
            self.statement_count,
            self.rows_read,
            self.rows_written,
            self.incomplete_meta_count,
            self.failed_request_count,
        )


@dataclass(frozen=True)
class D1UsageSnapshot:
    """Opaque checkpoint used to report one caller's share of a reused client."""

    owner: object
    request_count: int
    failed_request_count: int
    labels: tuple[tuple[str, tuple[int, int, int, int, int]], ...]


def _summarize_http_error(exc: error.HTTPError) -> str:
    detail = "Cloudflare D1 API rejected the request"
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = None

    messages: List[str] = []
    if isinstance(payload, dict):
        raw_errors = payload.get("errors") or []
        if isinstance(raw_errors, list):
            for item in raw_errors:
                if isinstance(item, str) and item.strip():
                    messages.append(item.strip())
                    continue
                if not isinstance(item, dict):
                    continue
                message = item.get("message")
                if not isinstance(message, str) or not message.strip():
                    continue
                code = item.get("code")
                prefix = f"[{code}] " if isinstance(code, int) else ""
                messages.append(prefix + message.strip())
    if messages:
        detail = "; ".join(messages)
    return f"D1 request failed with HTTP {exc.code}: {detail[:600]}"


class D1Client:
    def __init__(
        self,
        account_id: str,
        database_id: str,
        api_token: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.account_id = account_id
        self.database_id = database_id
        self.api_token = api_token
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.base_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/d1/database/{database_id}"
        )
        self._usage_owner = object()
        self._usage_lock = threading.Lock()
        self._usage_local = threading.local()
        self._usage_request_count = 0
        self._usage_failed_request_count = 0
        self._usage_labels: Dict[str, _D1UsageBucket] = {}

    @contextmanager
    def usage_label(self, label: str) -> Iterator[None]:
        """Apply a safe operation label to queries made inside this scope."""

        normalized = _normalize_usage_label(label)
        stack = getattr(self._usage_local, "label_stack", None)
        if stack is None:
            stack = []
            self._usage_local.label_stack = stack
        stack.append(normalized)
        try:
            yield
        finally:
            stack.pop()

    def usage_snapshot(self) -> D1UsageSnapshot:
        """Capture counters without exposing SQL, parameters, or credentials."""

        with self._usage_lock:
            labels = tuple(
                (label, bucket.as_tuple())
                for label, bucket in sorted(self._usage_labels.items())
            )
            return D1UsageSnapshot(
                owner=self._usage_owner,
                request_count=self._usage_request_count,
                failed_request_count=self._usage_failed_request_count,
                labels=labels,
            )

    def usage_summary(
        self,
        since: Optional[D1UsageSnapshot] = None,
    ) -> dict:
        """Return additive D1 row usage, optionally since a caller checkpoint."""

        current = self.usage_snapshot()
        if since is None:
            since = D1UsageSnapshot(
                owner=self._usage_owner,
                request_count=0,
                failed_request_count=0,
                labels=(),
            )
        if since.owner is not self._usage_owner:
            raise ValueError("D1 usage snapshot belongs to a different client")

        previous_labels = dict(since.labels)
        current_labels = dict(current.labels)
        labels: Dict[str, dict] = {}
        for label in sorted(set(previous_labels) | set(current_labels)):
            previous = previous_labels.get(label, (0, 0, 0, 0, 0))
            present = current_labels.get(label, (0, 0, 0, 0, 0))
            values = tuple(
                _usage_delta(current_value, previous_value)
                for current_value, previous_value in zip(present, previous)
            )
            if not any(values):
                continue
            labels[label] = {
                "statement_count": values[0],
                "rows_read": values[1],
                "rows_written": values[2],
                "incomplete_meta_count": values[3],
                "failed_request_count": values[4],
            }

        return {
            "request_count": _usage_delta(
                current.request_count,
                since.request_count,
            ),
            "failed_request_count": _usage_delta(
                current.failed_request_count,
                since.failed_request_count,
            ),
            "statement_count": sum(
                item["statement_count"] for item in labels.values()
            ),
            "rows_read": sum(item["rows_read"] for item in labels.values()),
            "rows_written": sum(
                item["rows_written"] for item in labels.values()
            ),
            "incomplete_meta_count": sum(
                item["incomplete_meta_count"] for item in labels.values()
            ),
            "labels": labels,
        }

    def _request(self, endpoint: str, payload: dict) -> dict:
        payload = {
            key: value for key, value in payload.items() if value is not None
        }
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}/{endpoint}",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise RuntimeError(_summarize_http_error(exc)) from exc

        if not data.get("success", False):
            raise RuntimeError(f"D1 query failed: {data}")

        errors = data.get("errors") or []
        if errors:
            raise RuntimeError(f"D1 query errors: {errors}")

        results = data.get("result", [])
        if not isinstance(results, list):
            raise RuntimeError(f"D1 query returned an invalid result shape: {data}")
        if not results:
            raise RuntimeError("D1 query returned no statement result")
        for index, item in enumerate(results):
            if not isinstance(item, dict):
                raise RuntimeError(
                    f"D1 query result {index} has an invalid shape: {item!r}"
                )
            item_errors = item.get("errors") or item.get("error") or []
            if item.get("success") is not True or item_errors:
                raise RuntimeError(
                    f"D1 query result {index} failed: "
                    f"success={item.get('success')!r}, errors={item_errors!r}"
                )

        return data

    def _request_with_usage(
        self,
        endpoint: str,
        payload: dict,
        labels: tuple[str, ...],
        *,
        expected_result_count: Optional[int] = None,
    ) -> dict:
        with self._usage_lock:
            self._usage_request_count += 1
        try:
            data = self._request(endpoint, payload)
            results = data["result"]
            if (
                expected_result_count is not None
                and len(results) != expected_result_count
            ):
                raise RuntimeError(
                    "D1 batch returned an unexpected statement result count: "
                    f"expected {expected_result_count}, got {len(results)}"
                )
        except Exception:
            self._record_failed_request(labels)
            raise

        if len(labels) == 1:
            result_labels = labels * len(results)
        elif len(labels) == len(results):
            result_labels = labels
        else:
            raise RuntimeError("D1 usage labels did not match statement results")
        self._record_statement_usage(results, result_labels)
        return data

    def query(
        self,
        sql: str,
        params: Optional[Iterable[object]] = None,
        *,
        label: Optional[str] = None,
    ) -> List[dict]:
        resolved_label = self._resolve_usage_label(label)
        data = self._request_with_usage(
            "query",
            {
                "sql": sql,
                "params": list(params or []),
            },
            (resolved_label,),
        )
        result = data.get("result", [])
        if len(result) == 1 and isinstance(result[0], dict) and "results" in result[0]:
            return result[0].get("results", [])
        return result

    def execute(
        self,
        sql: str,
        params: Optional[Iterable[object]] = None,
        *,
        label: Optional[str] = None,
    ) -> dict:
        resolved_label = self._resolve_usage_label(label)
        return self._request_with_usage(
            "query",
            {
                "sql": sql,
                "params": list(params or []),
            },
            (resolved_label,),
        )

    def batch(
        self,
        statements: Iterable[D1Statement],
        *,
        label: Optional[str] = None,
        labels: Optional[Iterable[str]] = None,
    ) -> List[dict]:
        batch = [
            {
                "sql": sql,
                "params": list(params or []),
            }
            for sql, params in statements
        ]
        if not batch:
            raise ValueError("D1 batch requires at least one statement")
        if label is not None and labels is not None:
            raise ValueError("D1 batch accepts either label or labels, not both")

        if labels is not None:
            resolved_labels = tuple(_normalize_usage_label(item) for item in labels)
            if len(resolved_labels) != len(batch):
                raise ValueError(
                    "D1 batch requires one usage label for each statement"
                )
        else:
            resolved_labels = (
                self._resolve_usage_label(label),
            ) * len(batch)

        data = self._request_with_usage(
            "query",
            {"batch": batch},
            resolved_labels,
            expected_result_count=len(batch),
        )
        results = data["result"]
        return results

    def batch_with_labels(
        self,
        statements: Iterable[D1Statement],
        labels: Iterable[str],
    ) -> List[dict]:
        """Compatibility hook for callers that also support query-only clients."""

        return self.batch(statements, labels=labels)

    def execute_script(self, sql_script: str) -> None:
        for statement in split_sql_statements(sql_script):
            self.execute(statement)

    def _resolve_usage_label(self, label: Optional[str]) -> str:
        if label is not None:
            return _normalize_usage_label(label)
        stack = getattr(self._usage_local, "label_stack", None)
        if stack:
            return stack[-1]
        return DEFAULT_D1_USAGE_LABEL

    def _record_failed_request(self, labels: tuple[str, ...]) -> None:
        with self._usage_lock:
            self._usage_failed_request_count += 1
            for label in set(labels):
                bucket = self._usage_labels.setdefault(label, _D1UsageBucket())
                bucket.failed_request_count += 1

    def _record_statement_usage(
        self,
        results: Iterable[Mapping[str, object]],
        labels: Iterable[str],
    ) -> None:
        with self._usage_lock:
            for result, label in zip(results, labels):
                bucket = self._usage_labels.setdefault(label, _D1UsageBucket())
                bucket.statement_count += 1
                meta = result.get("meta")
                if not isinstance(meta, Mapping):
                    bucket.incomplete_meta_count += 1
                    continue
                rows_read = _usage_metric(meta.get("rows_read"))
                rows_written = _usage_metric(meta.get("rows_written"))
                if rows_read is None or rows_written is None:
                    bucket.incomplete_meta_count += 1
                if rows_read is not None:
                    bucket.rows_read += rows_read
                if rows_written is not None:
                    bucket.rows_written += rows_written


@contextmanager
def d1_usage_label(client: object, label: str) -> Iterator[None]:
    """Label real D1 calls while leaving local/test-compatible clients unchanged."""

    scope = getattr(client, "usage_label", None)
    if callable(scope):
        with scope(label):
            yield
        return
    yield


def d1_usage_snapshot(client: object) -> Optional[D1UsageSnapshot]:
    snapshot = getattr(client, "usage_snapshot", None)
    if not callable(snapshot):
        return None
    value = snapshot()
    return value if isinstance(value, D1UsageSnapshot) else None


def d1_usage_summary(
    client: object,
    since: Optional[D1UsageSnapshot] = None,
) -> Optional[dict]:
    summary = getattr(client, "usage_summary", None)
    if not callable(summary):
        return None
    value = summary(since)
    return dict(value) if isinstance(value, Mapping) else None


def _normalize_usage_label(label: object) -> str:
    value = str(label or "").strip()
    if not _D1_USAGE_LABEL_PATTERN.fullmatch(value):
        raise ValueError(
            "D1 usage label must match [a-z0-9][a-z0-9._-]{0,63}"
        )
    return value


def _usage_metric(value: object) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and math.isfinite(value) and value >= 0:
        return int(value) if value.is_integer() else None
    return None


def _usage_delta(current: int, previous: int) -> int:
    delta = current - previous
    if delta < 0:
        raise ValueError("D1 usage snapshot is newer than the current counters")
    return delta


def split_sql_statements(sql_script: str) -> List[str]:
    statements: List[str] = []
    current: List[str] = []

    for line in sql_script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        candidate = "\n".join(current).strip()
        if sqlite3.complete_statement(candidate):
            statement = candidate.rstrip()
            if statement.endswith(";"):
                statement = statement[:-1].rstrip()
            if statement:
                statements.append(statement)
            current = []

    tail = "\n".join(current).strip().rstrip(";").strip()
    if tail:
        statements.append(tail)

    return statements
