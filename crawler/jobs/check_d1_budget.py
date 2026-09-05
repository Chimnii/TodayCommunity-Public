"""Read-only daily admission gate before schema, source requests, or model work."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from typing import Optional, Sequence

from crawler.d1 import (
    _RUN_BUDGET_DEFAULTS,
    _read_account_daily_usage,
    account_daily_stop_reason,
    run_budget_from_env,
)


def check_daily_budget(profiles: Sequence[str]) -> dict:
    budgets = [run_budget_from_env(profile) for profile in profiles]
    if all(budget is None for budget in budgets):
        return {
            "status": "allowed",
            "enabled": False,
            "profiles": list(profiles),
            "scope": "account_utc_day_admission",
            "stop_reason": None,
            "hard_quota_guarantee": False,
        }
    # Schema/source bootstrap is a separate process and has no client run meter.
    next_reads = sum(budget.rows_read for budget in budgets) + 2048
    next_writes = sum(budget.rows_written for budget in budgets) + 128
    result = {
        "status": "blocked",
        "enabled": True,
        "profiles": list(profiles),
        "scope": "account_utc_day_admission",
        "hard_quota_guarantee": False,
        "next_run_rows_read": next_reads,
        "next_run_rows_written": next_writes,
        "operational_rows_read_ceiling": 3_000_000,
        "operational_rows_written_ceiling": 80_000,
        "latency_rows_read_reserve": 100_000,
        "latency_rows_written_reserve": 10_000,
    }
    account_id = os.environ.get("TC_CF_ACCOUNT_ID", "").strip()
    token = os.environ.get("TC_CF_D1_ANALYTICS_TOKEN", "").strip()
    if not account_id or not token:
        return {**result, "stop_reason": "account_analytics_credentials_missing"}
    utc_day = datetime.now(timezone.utc).date().isoformat()
    try:
        usage = _read_account_daily_usage(account_id, token, utc_day)
    except Exception:
        return {**result, "stop_reason": "account_analytics_unavailable"}
    reason = account_daily_stop_reason(usage, next_reads, next_writes)
    return {**result, "status": "blocked" if reason else "allowed",
            "stop_reason": reason, "account_usage": usage}


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", action="append", required=True,
                        choices=tuple(_RUN_BUDGET_DEFAULTS))
    args = parser.parse_args(argv)
    try:
        result = check_daily_budget(args.profile)
    except ValueError:
        result = {"status": "blocked", "stop_reason": "invalid_run_budget"}
    print(json.dumps({"d1_daily_gate": result}, ensure_ascii=False))
    if result["status"] != "allowed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
