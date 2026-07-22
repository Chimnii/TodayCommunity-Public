from __future__ import annotations

import argparse
import json
from dataclasses import replace
from typing import Optional

from crawler.config import get_env, get_required_env, is_truthy
from crawler.d1 import D1Client
from crawler.fmkorea_browser import FmkoreaBrowserConfig, FmkoreaChromeSession
from crawler.jobs.run_all_sources import run_all_targets
from crawler.jobs.run_fmkorea_cycle import (
    CYCLE_MODE_HOT,
    run_fmkorea_target,
)
from crawler.targets import TargetBoard, get_target


FMKOREA_LOCAL_TARGET_KEYS = (
    "fmkorea-best-munich-search",
    "fmkorea-best-bayern-search",
    "fmkorea-bayern-board",
)


def iter_local_fmkorea_targets() -> tuple[TargetBoard, ...]:
    return tuple(get_target(target_key) for target_key in FMKOREA_LOCAL_TARGET_KEYS)


def _positive_int_arg(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the three FMKorea Hot feeds serially through one Google Chrome session."
        )
    )
    parser.add_argument("--mode", choices=(CYCLE_MODE_HOT,), required=True)
    parser.add_argument(
        "--max-pages-per-target",
        type=_positive_int_arg,
        default=None,
        help=(
            "Temporarily cap each target's Hot pages for a smoke run without "
            "raising its configured production limit."
        ),
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        default=is_truthy(get_env("TC_PERSIST", "0")),
    )
    display = parser.add_mutually_exclusive_group()
    display.add_argument(
        "--headed",
        dest="headless",
        action="store_false",
        help="Show the dedicated Chrome window (recommended for initial tests).",
    )
    display.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        help="Run installed Chrome in its current headless mode.",
    )
    parser.set_defaults(headless=None)
    return parser.parse_args()


def run_local_fmkorea_sources(
    *,
    mode: str,
    client: Optional[D1Client] = None,
    browser_config: Optional[FmkoreaBrowserConfig] = None,
    max_pages_per_target: Optional[int] = None,
) -> dict[str, object]:
    if mode != CYCLE_MODE_HOT:
        raise ValueError(
            f"unsupported local FMKorea sweep mode: {mode!r}; "
            f"expected {CYCLE_MODE_HOT!r}"
        )
    if max_pages_per_target is not None and (
        isinstance(max_pages_per_target, bool)
        or not isinstance(max_pages_per_target, int)
        or max_pages_per_target <= 0
    ):
        raise ValueError("max_pages_per_target must be a positive integer or None")

    config = browser_config or FmkoreaBrowserConfig.from_env()
    targets = iter_local_fmkorea_targets()
    if max_pages_per_target is not None:
        targets = tuple(
            replace(
                target,
                hot_max_pages=min(target.hot_max_pages, max_pages_per_target),
            )
            for target in targets
        )
    source_interval = max(target.request_interval_seconds for target in targets)
    if config.min_navigation_interval_seconds < source_interval:
        config = replace(
            config,
            min_navigation_interval_seconds=source_interval,
        )
    session = FmkoreaChromeSession(config)
    with session:

        def runner(
            target: TargetBoard,
            cycle_mode: str,
            cycle_client: Optional[D1Client],
        ) -> dict[str, object]:
            return run_fmkorea_target(
                target=target,
                mode=cycle_mode,
                client=cycle_client,
                fetcher=session,
            )

        result = run_all_targets(
            mode=mode,
            client=client,
            targets=targets,
            runner=runner,
        )
    if session.cleanup_warnings:
        result["browser_cleanup_warnings"] = list(session.cleanup_warnings)
        if result["status"] != "failed":
            result["failure_count"] = int(result["failure_count"]) + 1
        result["status"] = "failed"
    return result


def main() -> None:
    args = parse_args()
    client = None
    if args.persist:
        client = D1Client(
            account_id=get_required_env("TC_CF_ACCOUNT_ID"),
            database_id=get_required_env("TC_CF_DATABASE_ID"),
            api_token=get_required_env("TC_CF_API_TOKEN"),
        )
    browser_config = FmkoreaBrowserConfig.from_env(headless=args.headless)
    result = run_local_fmkorea_sources(
        mode=args.mode,
        client=client,
        browser_config=browser_config,
        max_pages_per_target=args.max_pages_per_target,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
