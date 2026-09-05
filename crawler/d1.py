from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, Iterator, List, Mapping, Optional, Tuple
from urllib import error, request


D1Statement = Tuple[str, Optional[Iterable[object]]]
DEFAULT_D1_USAGE_LABEL = "unlabeled"
_D1_USAGE_LABEL_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class D1BudgetExceeded(RuntimeError):
    """A local estimate stopped work before another D1 request was sent."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"D1 work stopped: {reason}")


class D1QuotaExceeded(RuntimeError):
    """Cloudflare rejected work because the account's daily quota is exhausted."""


@dataclass(frozen=True)
class D1RunBudget:
    rows_read: int
    rows_written: int
    control_rows_written: int = 64
    control_rows_read: int = 0
    request_rows_read: int = 2048
    request_rows_written: int = 128
    post_batch_rows_written: int = 192

    def __post_init__(self) -> None:
        if min(self.rows_read, self.rows_written, self.request_rows_read,
               self.request_rows_written, self.post_batch_rows_written) <= 0:
            raise ValueError("D1 budget limits and request estimates must be positive")
        if not 0 <= self.control_rows_written < self.rows_written:
            raise ValueError("D1 control reserve must fit inside the run write budget")
        if not 0 <= self.control_rows_read < self.rows_read:
            raise ValueError("D1 control reserve must fit inside the run read budget")


# These are independent run ceilings, not an account-wide daily quota ledger.
_RUN_BUDGET_DEFAULTS = {
    "community-hot": (20_000, 600),
    "fmkorea-hot": (20_000, 400),
    "community-backfill": (80_000, 800),
    "fmkorea-backfill": (80_000, 800),
    "game-news": (10_000, 500),
    "topic": (25_000, 300),
}
_CONTROL_LABELS = frozenset({"source.state", "run.log", "coverage", "topic.snapshot"})


def run_budget_from_env(profile: str) -> D1RunBudget:
    """Use explicit, positive overrides; a typo cannot silently disable a guard."""

    default_reads, default_writes = _RUN_BUDGET_DEFAULTS[profile]
    prefix = "TC_D1_" + profile.upper().replace("-", "_")

    def value(suffix: str, default: int) -> int:
        raw = os.environ.get(prefix + suffix, str(default))
        try:
            return int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid D1 budget setting: {prefix + suffix}") from exc

    return D1RunBudget(
        rows_read=value("_MAX_ROWS_READ", default_reads),
        rows_written=value("_MAX_ROWS_WRITTEN", default_writes),
        control_rows_read=8192 if profile == "topic" else 0,
    )


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
        run_budget: Optional[D1RunBudget] = None,
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
        self._budget = run_budget
        self._budget_stop_reason: Optional[str] = None
        self._quota_exhausted = False
        self._source_budget_start: Optional[D1UsageSnapshot] = None
        self._source_write_limit: Optional[int] = None
        self._source_stop_reason: Optional[str] = None
        self._account_usage: Optional[dict] = None
        self._account_usage_checked_at = 0.0
        self._account_usage_state = "not_checked"

    def configure_run_budget(self, profile: str) -> None:
        if self._budget is None:
            self._budget = run_budget_from_env(profile)

    @contextmanager
    def source_budget(self, rows_written: int) -> Iterator[None]:
        if self._source_budget_start is not None:
            raise ValueError("D1 source budget scopes cannot nest")
        self._source_budget_start = self.usage_snapshot()
        self._source_write_limit = rows_written
        self._source_stop_reason = None
        try:
            self.check_budget()
            yield
        finally:
            self._source_budget_start = None
            self._source_write_limit = None
            self._source_stop_reason = None

    def budget_status(self) -> dict:
        budget = self._budget
        return {
            "enabled": budget is not None,
            "scope": "client_run",
            "account_daily_enforced": False,
            "account_daily_gate": self._account_usage_state,
            "account_usage": self._account_usage,
            "request_estimates_are_hard_bounds": False,
            "max_rows_read": budget.rows_read if budget else None,
            "max_rows_written": budget.rows_written if budget else None,
            "control_rows_written": budget.control_rows_written if budget else None,
            "control_rows_read": budget.control_rows_read if budget else None,
            "stop_reason": self._budget_stop_reason or self._source_stop_reason,
            "quota_exhausted": self._quota_exhausted,
        }

    def check_budget(self, *, reserved: bool = False, next_rows_written: int = 0,
                     next_rows_read: int = 0) -> None:
        """Check before source/model work as well as before D1 operations."""
        if self._quota_exhausted:
            raise D1QuotaExceeded("D1 daily quota exhausted earlier in this run")
        reason = self._budget_stop_reason or self._source_stop_reason
        if reason and not (reserved and reason in {"rows_read", "rows_written", "source_rows_written"}):
            raise D1BudgetExceeded(reason)
        self._check_account_budget()
        self._check_request_budget({}, ("topic.snapshot" if reserved else "checkpoint",))
        if self._budget is not None:
            usage = self.usage_summary()
            for metric, estimate, reserve in (
                ("rows_read", next_rows_read, self._budget.control_rows_read),
                ("rows_written", next_rows_written, self._budget.control_rows_written),
            ):
                if estimate < 0:
                    raise ValueError("D1 next-work reservations cannot be negative")
                limit = getattr(self._budget, metric) - (0 if reserved else reserve)
                if usage[metric] + estimate > limit:
                    self._budget_stop_reason = metric
                    raise D1BudgetExceeded(metric)

    def _check_account_budget(self) -> None:
        enabled = os.environ.get("TC_D1_DAILY_GATE_ENABLED", "0") == "1"
        token = os.environ.get("TC_CF_D1_ANALYTICS_TOKEN", "").strip()
        if self._budget is None or not enabled:
            self._account_usage_state = "disabled"
            return
        if not token:
            self._account_usage_state = "missing_token"
            self._budget_stop_reason = "account_analytics_unavailable"
            raise D1BudgetExceeded(self._budget_stop_reason)
        day = datetime.now(timezone.utc).date().isoformat()
        if (self._account_usage is None or self._account_usage.get("utc_day") != day
                or time.monotonic() - self._account_usage_checked_at >= 60):
            self._account_usage_checked_at = time.monotonic()
            try:
                self._account_usage = _read_account_daily_usage(self.account_id, token, day)
                self._account_usage_state = "checked_with_latency_reserve"
            except Exception:
                self._account_usage = None
                self._account_usage_state = "unavailable"
                self._budget_stop_reason = "account_analytics_unavailable"
                raise D1BudgetExceeded(self._budget_stop_reason) from None
        # Analytics is delayed and other workers can run concurrently. Keep an
        # additional 10k writes/100k reads outside the whole next-run estimate.
        reason = account_daily_stop_reason(
            self._account_usage, self._budget.rows_read, self._budget.rows_written,
        )
        if reason:
            self._budget_stop_reason = reason
            raise D1BudgetExceeded(reason)

    def _check_request_budget(self, payload: dict, labels: tuple[str, ...]) -> None:
        if self._quota_exhausted:
            raise D1QuotaExceeded("D1 daily quota exhausted earlier in this run")
        budget = self._budget
        if budget is None:
            return
        control = bool(labels) and all(label in _CONTROL_LABELS for label in labels)
        # Control writes may use their reserve after an ordinary-work stop. They
        # remain forbidden after quota/unknown usage or after the full run ceiling.
        reason = self._budget_stop_reason or self._source_stop_reason
        if reason and (not control or reason == "usage_unknown"
                       or reason.startswith("account_")):
            raise D1BudgetExceeded(reason)
        usage = self.usage_summary()
        if usage["incomplete_meta_count"] or usage["failed_request_count"]:
            self._budget_stop_reason = "usage_unknown"
            raise D1BudgetExceeded("usage_unknown")
        statements = payload.get("batch") or ([payload] if payload else [])
        has_write = any(
            not re.match(r"^\s*(?:SELECT|EXPLAIN|PRAGMA)\b", str(item.get("sql", "")), re.I)
            for item in statements
        )
        read_estimate = budget.request_rows_read if statements else 0
        write_estimate = 0
        if has_write:
            if all(label == "topic.snapshot" for label in labels):
                write_estimate = 8 * len(statements)
            elif control:
                write_estimate = 16 * len(statements)
            elif "post.upsert" in labels:
                write_estimate = budget.post_batch_rows_written
            elif all(label in {"source.bootstrap", "game-news.bootstrap"}
                     for label in labels):
                write_estimate = 16 * len(statements)
            elif all(label == "topic" for label in labels):
                write_estimate = 8 * len(statements)
            elif all(label == "game-news.projection" for label in labels):
                # One included article and its statistics transition are atomic.
                # Multiplying a generic 128-row estimate by every guard statement
                # would prevent any projection under the 500-row game run budget.
                write_estimate = 64
            elif all(label in {"game-news.candidate", "game-news.evaluation"}
                     for label in labels):
                write_estimate = 32 * len(statements)
            else:
                write_estimate = budget.request_rows_written * len(statements)
        write_limit = budget.rows_written - (0 if control else budget.control_rows_written)
        read_limit = budget.rows_read - (0 if control else budget.control_rows_read)
        if usage["rows_read"] + read_estimate > read_limit:
            self._budget_stop_reason = "rows_read"
            raise D1BudgetExceeded("rows_read")
        if usage["rows_written"] + write_estimate > write_limit:
            self._budget_stop_reason = "rows_written"
            raise D1BudgetExceeded("rows_written")
        if self._source_budget_start is not None and not control:
            source_usage = self.usage_summary(self._source_budget_start)
            if source_usage["rows_written"] + write_estimate > self._source_write_limit:
                self._source_stop_reason = "source_rows_written"
                raise D1BudgetExceeded("source_rows_written")

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
            message = _summarize_http_error(exc)
            if _is_daily_quota_error(message):
                self._quota_exhausted = True
                raise D1QuotaExceeded(message) from exc
            raise RuntimeError(message) from exc

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
        self._check_request_budget(payload, labels)
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
        except Exception as exc:
            self._record_failed_request(labels)
            if _is_daily_quota_error(str(exc)):
                self._quota_exhausted = True
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


def configure_d1_run_budget(client: object, profile: str) -> None:
    configure = getattr(client, "configure_run_budget", None)
    if callable(configure):
        configure(profile)


def d1_budget_checkpoint(client: object, *, reserved: bool = False,
                         next_rows_written: int = 0, next_rows_read: int = 0) -> None:
    checkpoint = getattr(client, "check_budget", None)
    if callable(checkpoint):
        options = {}
        if reserved:
            options["reserved"] = True
        if next_rows_written:
            options["next_rows_written"] = next_rows_written
        if next_rows_read:
            options["next_rows_read"] = next_rows_read
        checkpoint(**options)


@contextmanager
def d1_source_budget(client: object, rows_written: int) -> Iterator[None]:
    scope = getattr(client, "source_budget", None)
    if callable(scope):
        with scope(rows_written):
            yield
        return
    yield


def d1_budget_status(client: object) -> Optional[dict]:
    status = getattr(client, "budget_status", None)
    if callable(status):
        return status()
    return None


def attach_d1_failure_usage(exc: Exception, client: object) -> None:
    """Carry safe counters to the CLI's one failure record; do not print twice."""
    usage = d1_usage_summary(client)
    if usage is not None:
        exc.d1_usage = usage
    budget = d1_budget_status(client)
    if budget is not None:
        exc.d1_budget = budget


def d1_failure_report(exc: Exception) -> dict:
    result = {"status": "failed", "error_type": type(exc).__name__}
    for key in ("d1_usage", "d1_budget"):
        value = getattr(exc, key, None)
        if isinstance(value, dict):
            result[key] = value
    if isinstance(exc, D1BudgetExceeded):
        result["stop_reason"] = exc.reason
    elif isinstance(exc, D1QuotaExceeded):
        result["stop_reason"] = "daily_quota"
    return result


def _is_daily_quota_error(message: str) -> bool:
    value = message.lower()
    return "d1" in value and "daily" in value and "limit" in value and any(
        word in value for word in ("exceeded", "exhausted")
    )


def account_daily_stop_reason(usage: dict, next_reads: int, next_writes: int) -> Optional[str]:
    # This is a delayed telemetry admission gate, not an atomic quota ledger.
    limits = (("rows_read", 3_000_000, 100_000, next_reads),
              ("rows_written", 80_000, 10_000, next_writes))
    for metric, ceiling, latency_reserve, run_reserve in limits:
        if usage[metric] + run_reserve + latency_reserve > ceiling:
            return "account_" + metric
    return None


def publish_d1_stop_output(exc: Exception) -> None:
    """Block independent work only for an account stop or unknown D1 usage."""
    budget = getattr(exc, "d1_budget", {})
    usage = getattr(exc, "d1_usage", {})
    local_reasons = {"rows_read", "rows_written", "source_rows_written"}
    reason = exc.reason if isinstance(exc, D1BudgetExceeded) else None
    budget_reason = budget.get("stop_reason") if isinstance(budget, dict) else None
    stopped = (
        isinstance(exc, D1QuotaExceeded)
        or (reason is not None and reason not in local_reasons)
        or (budget_reason is not None and budget_reason not in local_reasons)
        or (isinstance(budget, dict) and bool(budget.get("quota_exhausted")))
        or (isinstance(usage, dict) and bool(
            usage.get("incomplete_meta_count") or usage.get("failed_request_count")))
    )
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"d1_stop={'true' if stopped else 'false'}\n")


def _read_account_daily_usage(account_id: str, token: str, utc_day: str) -> dict:
    query = """query($accountTag:string!,$date:Date!){viewer{
      accounts(filter:{accountTag:$accountTag}){
        d1AnalyticsAdaptiveGroups(limit:1,filter:{date_geq:$date,date_leq:$date}){
          sum{rowsRead rowsWritten}
        }
      }
    }}"""
    body = json.dumps({"query": query, "variables": {
        "accountTag": account_id, "date": utc_day,
    }}).encode("utf-8")
    http_request = request.Request(
        "https://api.cloudflare.com/client/v4/graphql", data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=15) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("errors"):
        raise RuntimeError("D1 account analytics unavailable")
    accounts = payload["data"]["viewer"]["accounts"]
    if len(accounts) != 1:
        raise RuntimeError("D1 account analytics returned an invalid account count")
    groups = accounts[0]["d1AnalyticsAdaptiveGroups"]
    if len(groups) > 1:
        raise RuntimeError("D1 account analytics returned an invalid aggregate")
    sums = groups[0]["sum"] if groups else {"rowsRead": 0, "rowsWritten": 0}
    reads, writes = _usage_metric(sums.get("rowsRead")), _usage_metric(sums.get("rowsWritten"))
    if reads is None or writes is None:
        raise RuntimeError("D1 account analytics returned incomplete row metrics")
    return {"utc_day": utc_day, "rows_read": reads, "rows_written": writes}


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
