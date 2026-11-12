"""Non-mutating collection evidence for notification policy (not quality policy).

Only bounded, allowlisted component identifiers/statuses reach Actions metadata.
Article text, URLs, error strings and credentials never become workflow commands.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping

PREFIX = "Collection health v1: "
CONTRACT = "Collection health contract v1"
STATES = {"success", "failure", "deferred", "critical"}
AI_SOURCES = {"hermes", "paperclip", "openclaw", "gastown", "pal", "langchain",
              "simon-willison", "anthropic", "gpters", "latent-space", "massgen"}
NORMAL_BUDGET_ERRORS = {None, "", "Source request budget exhausted", "Discovery deadline exhausted"}


def result_health(result: Mapping, lane: str) -> dict[str, str]:
    """Preserve partial outages per source; editorial decisions aren't failures."""
    status = result.get("status")
    if status in {"not_due", "already_running", "skipped", "cooldown"}:
        return {"pipeline": "deferred"}
    if result.get("error_type") in {"IntegrityError"}:
        return {"pipeline": "critical"}
    if result.get("error_type") or (status == "failed" and not result.get("discovery") and not result.get("results")):
        return {"pipeline": "failure"}
    health = {"pipeline": "success"}
    if lane == "ai":
        discovery = result.get("discovery", {})
        for source in discovery.get("sources", []):
            name = source.get("source")
            if name not in AI_SOURCES:
                raise ValueError("Unrecognized AI health source")
            pages = source.get("pages", [])
            fetches = [f for f in discovery.get("fetches", []) if f.get("source") == name]
            failed = any(p.get("status") in {"failed", "blocked", "partial", "oversized"} for p in pages)
            failed |= any(f.get("status") not in {"ok", "cached", "deferred_budget", "budget"}
                          and (f.get("status") != "partial" or f.get("error") not in NORMAL_BUDGET_ERRORS)
                          for f in fetches)
            # A retained source failure on cooldown must not masquerade as
            # recovery, nor add another failed *attempt* on every hourly tick.
            unresolved = any(p.get("scope", {}).get("failures") for p in pages)
            budget = (source.get("model_input_status") == "deferred"
                      or any(p.get("status") in {"budget", "deferred_budget"} for p in pages)
                      or any(f.get("status") in {"budget", "deferred_budget"}
                             or (f.get("status") == "partial" and f.get("error") in NORMAL_BUDGET_ERRORS)
                             for f in fetches))
            attempted = any(p.get("status") in {"ok", "empty"} for p in pages) or any(
                f.get("status") in {"ok", "cached"} for f in fetches)
            if source.get("status") == "failed":
                failed = True
            health["source:" + name] = "failure" if failed else (
                "deferred" if unresolved or budget or not attempted else "success")
        if not discovery.get("sources") and status == "partial":
            health["pipeline"] = "failure"  # Unexplained partial cannot disappear.
        elif not discovery.get("sources") and status == "failed":
            health["pipeline"] = "failure"
    elif lane == "game":
        # Missing provenance for a configured site is a coverage failure even
        # when other sites produced useful articles. Zero includes is normal.
        for name in result.get("observed_sites", []):
            health["source:" + name] = "success"
        for name in result.get("missing_sites", []):
            health["source:" + name] = "failure"
    elif lane in {"fm", "dc"}:
        for row in result.get("results", []):
            name = row.get("target") or row.get("source_key")
            if not isinstance(name, str):
                health["pipeline"] = "failure"
                continue
            s = row.get("status")
            partial_phases = [p for p in row.get("phases", []) if p.get("status") == "partial"]
            budget_partial = bool(partial_phases) and all(p.get("stop_reason") in {
                "cycle_deadline", "hot_deadline",
            } for p in partial_phases)
            health["source:" + name] = (
                "failure" if s in {"failed", "blocked"} or row.get("persistence_error") or row.get("persistence_warnings") else
                ("deferred" if budget_partial else "failure") if s == "partial" else
                "deferred" if s in {"cooldown", "skipped", "deferred", "budget_exhausted"} else
                "success" if s == "completed" else "failure")
        if result.get("browser_cleanup_warnings"):
            health["pipeline"] = "failure"
    else:
        raise ValueError("Unknown collection lane")
    source_states = [v for k, v in health.items() if k.startswith("source:")]
    if (source_states and all(s == "deferred" for s in source_states)
            and not result.get("candidate_count") and health["pipeline"] == "success"):
        health["pipeline"] = "deferred"
    return validate_health(health)


def validate_health(health: object) -> dict[str, str]:
    import re
    if not isinstance(health, dict) or not health or len(health) > 32:
        raise ValueError("Invalid collection health object")
    if any(not isinstance(k, str) or not re.fullmatch(r"(?:pipeline|source:[a-z0-9_-]{1,64})", k)
           or not isinstance(v, str) or v not in STATES for k, v in health.items()):
        raise ValueError("Invalid collection health component")
    return dict(health)


def _publish_health(result: Mapping, lane: str) -> None:
    health = result_health(result, lane)
    encoded = json.dumps(health, sort_keys=True, separators=(",", ":"))
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write("collection_health=" + encoded + "\n")
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as stream:
            stream.write("\nCollection evidence (notification policy is separate):\n\n")
            stream.write("```json\n" + encoded + "\n```\n")


def publish_health(result: Mapping, lane: str) -> None:
    # Diagnostics must never overwrite the original CLI result/exception. A
    # missing marker is explicitly counted by the new workflow, not assumed OK.
    try:
        _publish_health(result, lane)
    except (OSError, ValueError, TypeError, KeyError):
        print("Collection health metadata unavailable; see the original collection result.")
