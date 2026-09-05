from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional

from crawler.config import get_env, get_required_env, is_truthy
from crawler.d1 import (
    D1Client,
    D1BudgetExceeded,
    D1QuotaExceeded,
    configure_d1_run_budget,
    d1_budget_checkpoint,
    d1_budget_status,
    d1_source_budget,
    d1_usage_snapshot,
    d1_usage_summary,
)
from crawler.jobs.run_cycle import (
    CYCLE_MODE_BACKFILL,
    CYCLE_MODE_HOT,
    CrawlCycle,
    CycleConfig,
)
from crawler.jobs.scan_new_posts import record_run, upsert_source, utc_now
from crawler.runtime import CycleRuntime
from crawler.state import SourceState, get_source_state, save_source_state
from crawler.targets import TargetBoard, get_target
from crawler.timestamps import canonical_utc


VALID_MODES = (CYCLE_MODE_HOT, CYCLE_MODE_BACKFILL)
FAILURE_STATUSES = {"blocked", "failed"}
GITHUB_SCHEDULED_TARGET_KEYS = (
    "dcinside-singularity",
    "dcinside-ai-utilize",
    "dcinside-zeus-pride",
)


def iter_github_scheduled_targets() -> tuple[TargetBoard, ...]:
    """Return only sources allowed on GitHub-hosted scheduled runners."""

    return tuple(get_target(target_key) for target_key in GITHUB_SCHEDULED_TARGET_KEYS)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one serial GitHub-scheduled sweep across DC sources."
    )
    parser.add_argument("--mode", choices=VALID_MODES, required=True)
    parser.add_argument(
        "--persist",
        action="store_true",
        default=is_truthy(get_env("TC_PERSIST", "0")),
    )
    return parser.parse_args()


def _env_float(name: str, fallback: float) -> float:
    raw_value = get_env(name, str(fallback))
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        return float(fallback)


def _env_int(name: str, fallback: int) -> int:
    raw_value = get_env(name, str(fallback))
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        return int(fallback)


def dc_cycle_config(target: TargetBoard, mode: str) -> CycleConfig:
    """Build a per-target DC config while preserving singularity overrides."""

    hot_seconds = float(target.hot_max_seconds)
    cycle_seconds = (
        hot_seconds if mode == CYCLE_MODE_HOT else float(target.backfill_max_seconds)
    )
    lookback_minutes = float(target.hot_lookback_minutes)
    request_interval = float(target.request_interval_seconds)
    finalization_hours = float(target.finalization_age_hours)
    cooldown_hours = float(target.block_cooldown_hours)
    transient_attempts = 2

    # Existing one-off operator inputs intentionally remain scoped to the
    # original archive. Every other target keeps the policy in targets.py.
    if target.key == "dcinside-singularity":
        hot_seconds = _env_float("TC_HOT_MAX_SECONDS", hot_seconds)
        cycle_seconds = _env_float("TC_CYCLE_MAX_SECONDS", cycle_seconds)
        lookback_minutes = _env_float("TC_HOT_LOOKBACK_MINUTES", lookback_minutes)
        request_interval = _env_float("TC_REQUEST_INTERVAL_SECONDS", request_interval)
        finalization_hours = _env_float(
            "TC_FINALIZATION_AGE_HOURS", finalization_hours
        )
        cooldown_hours = _env_float("TC_BLOCK_COOLDOWN_HOURS", cooldown_hours)
        transient_attempts = _env_int("TC_TRANSIENT_FETCH_ATTEMPTS", 2)

    if mode == CYCLE_MODE_HOT:
        cycle_seconds = max(hot_seconds, cycle_seconds)
        deep_reserved_seconds = 0.0
    else:
        hot_seconds = min(hot_seconds, max(1.0, cycle_seconds - 2.0))
        default_reservation = min(300.0, cycle_seconds / 2.0)
        deep_reserved_seconds = min(
            default_reservation,
            max(1.0, cycle_seconds - hot_seconds - 1.0),
        )
        if target.key == "dcinside-singularity":
            deep_reserved_seconds = _env_float(
                "TC_DEEP_RESERVED_SECONDS", deep_reserved_seconds
            )

    return CycleConfig(
        finalization_age_hours=finalization_hours,
        hot_lookback_minutes=lookback_minutes,
        hot_max_seconds=hot_seconds,
        cycle_max_seconds=cycle_seconds,
        min_request_interval_seconds=request_interval,
        deep_reserved_seconds=deep_reserved_seconds,
        block_cooldown_hours=cooldown_hours,
        transient_fetch_attempts=transient_attempts,
    )


def run_dc_target(
    target: TargetBoard,
    mode: str,
    client: Optional[D1Client],
) -> Dict[str, object]:
    config = dc_cycle_config(target, mode)
    runtime = CycleRuntime(
        min_request_interval_seconds=config.min_request_interval_seconds,
        total_seconds=config.cycle_max_seconds,
        hot_seconds=config.hot_max_seconds,
    )
    return CrawlCycle(
        target=target,
        config=config,
        runtime=runtime,
        client=client,
        mode=mode,
    ).run()


def run_target(
    target: TargetBoard,
    mode: str,
    client: Optional[D1Client],
) -> Dict[str, object]:
    if target.collector_kind == "dcinside-board":
        return run_dc_target(target, mode, client)
    if target.collector_kind in {"fmkorea-search", "fmkorea-board"}:
        from crawler.jobs.run_fmkorea_cycle import run_fmkorea_target

        return run_fmkorea_target(target=target, mode=mode, client=client)
    raise ValueError(
        f"Target {target.key!r} uses unsupported collector "
        f"{target.collector_kind!r}."
    )


def _record_origin_skip(
    client: Optional[D1Client],
    target: TargetBoard,
    mode: str,
    blocked_by: str,
    status: str,
) -> None:
    if client is None:
        return
    started_at = utc_now()
    record_run(
        client,
        target=target,
        status=status,
        scanned_pages=0,
        scanned_posts=0,
        matched_posts=0,
        run_started_at=started_at,
        error_message=(
            f"Skipped without another source request because {blocked_by} "
            f"reported an origin-level block."
        ),
        run_type=f"{mode}_origin_cooldown",
    )


def _propagate_origin_cooldown(
    client: Optional[D1Client],
    targets: Iterable[TargetBoard],
    reason: str,
) -> None:
    """Copy an observed origin block to every feed without mixing cursors."""

    if client is None:
        return
    blocked_at = datetime.now(timezone.utc).replace(microsecond=0)
    blocked_at_text = canonical_utc(blocked_at)
    for target in targets:
        # record_run/upsert_source will create missing sources for skipped feeds.
        upsert_source(client, target, blocked_at_text)
        state = get_source_state(client, target.key) or SourceState(
            source_key=target.key
        )
        state.last_blocked_at = blocked_at_text
        state.last_block_reason = reason[:500]
        state.blocked_until = canonical_utc(
            blocked_at + timedelta(hours=target.block_cooldown_hours)
        )
        save_source_state(client, state)


def run_all_targets(
    *,
    mode: str,
    client: Optional[D1Client] = None,
    targets: Optional[Iterable[TargetBoard]] = None,
    scheduled_at: Optional[datetime] = None,
    runner: Callable[
        [TargetBoard, str, Optional[D1Client]], Dict[str, object]
    ] = run_target,
) -> Dict[str, object]:
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported sweep mode: {mode!r}")

    d1_usage_start = d1_usage_snapshot(client) if client else None
    now = scheduled_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("scheduled_at must have an explicit timezone")
    started_at = canonical_utc(now)
    target_list = tuple(
        iter_github_scheduled_targets() if targets is None else targets
    )
    fmkorea_only = bool(target_list) and all(
        target.collector_kind.startswith("fmkorea-") for target in target_list
    )
    profile = f"{'fmkorea' if fmkorea_only else 'community'}-{mode}"
    configure_d1_run_budget(client, profile)
    rotation_period = None
    rotation_slot = None
    # Preserve configured source order in normal collection. Rotation only
    # belongs to the optional shared-budget experiment.
    if target_list and (d1_budget_status(client) or {}).get("enabled"):
        rotation_period = 1800 if mode == CYCLE_MODE_HOT else 6 * 3600
        rotation_slot = int(now.timestamp()) // rotation_period
        offset = rotation_slot % len(target_list)
        target_list = target_list[offset:] + target_list[:offset]
    source_write_limit = (250 if fmkorea_only else 450) if mode == CYCLE_MODE_HOT else 600
    blocked_origins: Dict[str, Dict[str, str]] = {}
    results: List[Dict[str, object]] = []

    for target in target_list:
        try:
            d1_budget_checkpoint(client)
        except (D1BudgetExceeded, D1QuotaExceeded) as exc:
            results.append({
                "target": target.key,
                "archive": target.archive_key,
                "status": "failed",
                "stop_reason": getattr(exc, "reason", "daily_quota"),
                "source_requested": False,
            })
            continue
        origin_block = blocked_origins.get(target.origin_key)
        if origin_block:
            blocked_by = origin_block["target"]
            skip_status = origin_block["status"]
            skip_result: Dict[str, object] = {
                "target": target.key,
                "archive": target.archive_key,
                "status": skip_status,
                "stop_reason": "origin_blocked",
                "blocked_by": blocked_by,
            }
            try:
                _record_origin_skip(
                    client,
                    target,
                    mode,
                    blocked_by,
                    skip_status,
                )
            except Exception as exc:
                skip_result["status"] = "failed"
                skip_result["persistence_error"] = (
                    f"Could not record origin cooldown: {type(exc).__name__}: {exc}"
                )
            results.append(skip_result)
            continue

        try:
            with d1_source_budget(client, source_write_limit):
                result = dict(runner(target, mode, client))
                # A cycle can return its own failed result rather than raising.
                # Inspect the latch before allowing the next source to run.
                try:
                    d1_budget_checkpoint(client)
                except (D1BudgetExceeded, D1QuotaExceeded) as exc:
                    result["status"] = "failed"
                    result["stop_reason"] = getattr(exc, "reason", "daily_quota")
        except Exception as exc:  # keep later independent origins observable
            result = {
                "target": target.key,
                "archive": target.archive_key,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
            if isinstance(exc, (D1BudgetExceeded, D1QuotaExceeded)):
                result["stop_reason"] = getattr(exc, "reason", "daily_quota")
        result.setdefault("target", target.key)
        result.setdefault("archive", target.archive_key)
        results.append(result)

        result_status = str(result.get("status", ""))
        if result_status in {"blocked", "cooldown"}:
            blocked_origins[target.origin_key] = {
                "target": target.key,
                "status": result_status,
            }
            if result_status == "blocked":
                try:
                    _propagate_origin_cooldown(
                        client,
                        (
                            candidate
                            for candidate in target_list
                            if candidate.origin_key == target.origin_key
                        ),
                        str(
                            result.get("blocked_reason")
                            or result.get("error")
                            or f"{target.key} reported an origin-level block"
                        ),
                    )
                except Exception as exc:
                    result["persistence_error"] = (
                        "Could not propagate origin cooldown: "
                        f"{type(exc).__name__}: {exc}"
                    )

    failure_count = sum(
        1 for result in results if str(result.get("status", "")) in FAILURE_STATUSES
    )
    sweep_result: Dict[str, object] = {
        "mode": mode,
        "started_at": started_at,
        "finished_at": canonical_utc(datetime.now(timezone.utc)),
        "status": "failed" if failure_count else "completed",
        "target_count": len(results),
        "planned_source_order": [target.key for target in target_list],
        "source_rotation_slot": rotation_slot,
        "source_rotation_period_seconds": rotation_period,
        "failure_count": failure_count,
        "results": results,
    }
    if client:
        usage = d1_usage_summary(client, d1_usage_start)
        if usage is not None:
            sweep_result["d1_usage"] = usage
        budget = d1_budget_status(client)
        if budget is not None:
            sweep_result["d1_budget"] = budget
    return sweep_result


def main() -> None:
    args = parse_args()
    client = None
    if args.persist:
        client = D1Client(
            account_id=get_required_env("TC_CF_ACCOUNT_ID"),
            database_id=get_required_env("TC_CF_DATABASE_ID"),
            api_token=get_required_env("TC_CF_API_TOKEN"),
        )

    result = run_all_targets(mode=args.mode, client=client)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
