from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional

from crawler.config import get_env, get_required_env, is_truthy
from crawler.d1 import D1Client
from crawler.jobs.run_cycle import (
    CYCLE_MODE_BACKFILL,
    CYCLE_MODE_HOT,
    CrawlCycle,
    CycleConfig,
)
from crawler.jobs.scan_new_posts import record_run, upsert_source, utc_now
from crawler.runtime import CycleRuntime
from crawler.state import SourceState, get_source_state, save_source_state
from crawler.targets import TargetBoard, iter_targets


VALID_MODES = (CYCLE_MODE_HOT, CYCLE_MODE_BACKFILL)
FAILURE_STATUSES = {"blocked", "failed"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run one serial TodayCommunity sweep across every source."
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
    for target in targets:
        # record_run/upsert_source will create missing sources for skipped feeds.
        upsert_source(client, target, blocked_at.isoformat())
        state = get_source_state(client, target.key) or SourceState(
            source_key=target.key
        )
        state.last_blocked_at = blocked_at.isoformat()
        state.last_block_reason = reason[:500]
        state.blocked_until = (
            blocked_at + timedelta(hours=target.block_cooldown_hours)
        ).isoformat()
        save_source_state(client, state)


def run_all_targets(
    *,
    mode: str,
    client: Optional[D1Client] = None,
    targets: Optional[Iterable[TargetBoard]] = None,
    runner: Callable[
        [TargetBoard, str, Optional[D1Client]], Dict[str, object]
    ] = run_target,
) -> Dict[str, object]:
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported sweep mode: {mode!r}")

    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    target_list = tuple(targets or iter_targets())
    blocked_origins: Dict[str, Dict[str, str]] = {}
    results: List[Dict[str, object]] = []

    for target in target_list:
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
            result = dict(runner(target, mode, client))
        except Exception as exc:  # keep later independent origins observable
            result = {
                "target": target.key,
                "archive": target.archive_key,
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
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
    return {
        "mode": mode,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "status": "failed" if failure_count else "completed",
        "target_count": len(results),
        "failure_count": failure_count,
        "results": results,
    }


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
