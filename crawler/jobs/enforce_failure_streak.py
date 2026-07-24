from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from itertools import chain
from typing import Callable, Iterable, Iterator, Mapping, Sequence
from urllib import error, parse, request


DEFAULT_THRESHOLD = 3
DEFAULT_SUCCESS_MARKER = "Failure streak: counted success"
DEFAULT_IGNORED_MARKER = "Failure streak: ignored run"
PER_PAGE = 100


class GitHubApiError(RuntimeError):
    """Raised when GitHub Actions history cannot be read safely."""


class AttemptKind(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    IGNORED = "ignored"
    BOUNDARY = "boundary"


@dataclass(frozen=True)
class GateDecision:
    current_kind: AttemptKind
    consecutive_failures: int
    threshold: int

    @property
    def should_fail(self) -> bool:
        return self.consecutive_failures >= self.threshold


def _normalized_conclusion(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GitHubApiError("GitHub returned a non-string conclusion")
    return value.strip().lower()


def _step_with_name(
    job: Mapping[str, object],
    step_name: str,
) -> Mapping[str, object] | None:
    raw_steps = job.get("steps", [])
    if not isinstance(raw_steps, list):
        raise GitHubApiError("GitHub returned an invalid job steps payload")
    matches = [
        step
        for step in raw_steps
        if isinstance(step, Mapping) and step.get("name") == step_name
    ]
    if len(matches) > 1:
        raise GitHubApiError(f"job contains duplicate marker step: {step_name}")
    return matches[0] if matches else None


def classify_attempt(
    job: Mapping[str, object] | None,
    *,
    success_marker: str = DEFAULT_SUCCESS_MARKER,
    ignored_marker: str = DEFAULT_IGNORED_MARKER,
) -> AttemptKind:
    """Classify one attempt job without relying on its workflow conclusion."""

    if job is None:
        return AttemptKind.BOUNDARY

    conclusion = _normalized_conclusion(job.get("conclusion"))
    status = job.get("status")
    if status != "completed" or conclusion is None:
        raise GitHubApiError("attempt job is not complete")
    if conclusion == "skipped":
        return AttemptKind.IGNORED

    success_step = _step_with_name(job, success_marker)
    ignored_step = _step_with_name(job, ignored_marker)
    success_conclusion = (
        _normalized_conclusion(success_step.get("conclusion"))
        if success_step is not None
        else None
    )
    ignored_conclusion = (
        _normalized_conclusion(ignored_step.get("conclusion"))
        if ignored_step is not None
        else None
    )

    if ignored_conclusion == "success":
        if success_conclusion == "success":
            raise GitHubApiError(
                "attempt job contains both successful and ignored markers"
            )
        return AttemptKind.IGNORED

    raw_steps = job.get("steps", [])
    assert isinstance(raw_steps, list)
    failed_step = any(
        isinstance(step, Mapping)
        and _normalized_conclusion(step.get("conclusion"))
        in {
            "action_required",
            "cancelled",
            "failure",
            "startup_failure",
            "stale",
            "timed_out",
        }
        for step in raw_steps
    )
    failed_job = conclusion in {
        "action_required",
        "cancelled",
        "failure",
        "neutral",
        "startup_failure",
        "stale",
        "timed_out",
    }
    if failed_step or failed_job:
        return AttemptKind.FAILURE
    if success_conclusion == "success" and conclusion == "success":
        return AttemptKind.SUCCESS
    if conclusion == "success":
        # A successful pre-marker job belongs to the legacy workflow. It is a
        # safe boundary so a streak never crosses the policy deployment.
        return AttemptKind.BOUNDARY
    raise GitHubApiError(f"unsupported attempt job conclusion: {conclusion}")


def count_consecutive_failures(
    attempts_newest_first: Iterable[AttemptKind],
) -> int:
    count = 0
    for attempt in attempts_newest_first:
        if attempt is AttemptKind.FAILURE:
            count += 1
        elif attempt is AttemptKind.IGNORED:
            continue
        else:
            break
    return count


class GitHubActionsClient:
    def __init__(
        self,
        *,
        api_url: str,
        repository: str,
        token: str,
        timeout_seconds: float = 20.0,
        opener: Callable[..., object] = request.urlopen,
    ) -> None:
        if not token:
            raise GitHubApiError("GITHUB_TOKEN is required")
        if repository.count("/") != 1:
            raise GitHubApiError("GITHUB_REPOSITORY must be owner/repository")
        self._base_url = api_url.rstrip("/")
        self._repository = repository
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    def _get_json(
        self,
        path: str,
        query: Mapping[str, object] | None = None,
    ) -> Mapping[str, object]:
        encoded_query = parse.urlencode(query or {})
        url = f"{self._base_url}/repos/{self._repository}{path}"
        if encoded_query:
            url = f"{url}?{encoded_query}"
        api_request = request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "TodayCommunity-failure-streak-gate",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            method="GET",
        )
        try:
            response_context = self._opener(
                api_request,
                timeout=self._timeout_seconds,
            )
            with response_context as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise GitHubApiError(
                f"GitHub API returned HTTP {exc.code} for {path}"
            ) from exc
        except error.URLError as exc:
            raise GitHubApiError(
                f"GitHub API request failed for {path}: {exc.reason}"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GitHubApiError(
                f"GitHub API returned invalid JSON for {path}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise GitHubApiError(f"GitHub API returned an invalid object for {path}")
        return payload

    def get_run(self, run_id: int) -> Mapping[str, object]:
        return self._get_json(f"/actions/runs/{run_id}")

    def get_jobs(self, run_id: int) -> list[Mapping[str, object]]:
        return list(
            self._iter_paginated(
                f"/actions/runs/{run_id}/jobs",
                collection_key="jobs",
                extra_query={"filter": "latest"},
            )
        )

    def get_attempt_jobs(
        self,
        run_id: int,
        attempt_number: int,
    ) -> list[Mapping[str, object]]:
        return list(
            self._iter_paginated(
                f"/actions/runs/{run_id}/attempts/{attempt_number}/jobs",
                collection_key="jobs",
            )
        )

    def iter_workflow_run_pages(
        self,
        workflow_id: int,
        branch: str,
    ) -> Iterator[list[Mapping[str, object]]]:
        yield from self._iter_paginated_pages(
            f"/actions/workflows/{workflow_id}/runs",
            collection_key="workflow_runs",
            extra_query={"branch": branch},
        )

    def _iter_paginated(
        self,
        path: str,
        *,
        collection_key: str,
        extra_query: Mapping[str, object] | None = None,
    ) -> Iterator[Mapping[str, object]]:
        for page in self._iter_paginated_pages(
            path,
            collection_key=collection_key,
            extra_query=extra_query,
        ):
            yield from page

    def _iter_paginated_pages(
        self,
        path: str,
        *,
        collection_key: str,
        extra_query: Mapping[str, object] | None = None,
    ) -> Iterator[list[Mapping[str, object]]]:
        page_number = 1
        while True:
            query = dict(extra_query or {})
            query.update({"per_page": PER_PAGE, "page": page_number})
            payload = self._get_json(path, query)
            raw_items = payload.get(collection_key)
            if not isinstance(raw_items, list):
                raise GitHubApiError(
                    f"GitHub API omitted the {collection_key} collection"
                )
            if any(not isinstance(item, Mapping) for item in raw_items):
                raise GitHubApiError(
                    f"GitHub API returned invalid items in {collection_key}"
                )
            items = list(raw_items)
            yield items

            total_count = payload.get("total_count")
            if total_count is not None and (
                not isinstance(total_count, int) or total_count < 0
            ):
                raise GitHubApiError("GitHub API returned an invalid total_count")
            if len(items) < PER_PAGE:
                break
            if isinstance(total_count, int) and page_number * PER_PAGE >= total_count:
                break
            page_number += 1


def _find_attempt_job(
    jobs: Sequence[Mapping[str, object]],
    attempt_job_name: str,
) -> Mapping[str, object] | None:
    matches = [job for job in jobs if job.get("name") == attempt_job_name]
    if len(matches) > 1:
        raise GitHubApiError(
            f"run contains duplicate attempt jobs named {attempt_job_name}"
        )
    return matches[0] if matches else None


def classify_attempt_jobs(
    jobs: Sequence[Mapping[str, object]],
    *,
    attempt_job_name: str,
    gate_job_name: str,
    success_marker: str = DEFAULT_SUCCESS_MARKER,
    ignored_marker: str = DEFAULT_IGNORED_MARKER,
) -> AttemptKind:
    attempt_job = _find_attempt_job(jobs, attempt_job_name)
    if attempt_job is not None:
        return classify_attempt(
            attempt_job,
            success_marker=success_marker,
            ignored_marker=ignored_marker,
        )
    if not jobs or all(job.get("name") == gate_job_name for job in jobs):
        # A cancelled run may never schedule its attempt job. Likewise, a
        # currently running ignored workflow may expose only its gate job.
        # Neither observation is a collection result, so it cannot reset or
        # increment the streak.
        return AttemptKind.IGNORED
    # Non-empty jobs from the pre-policy workflow are a safe history boundary.
    return AttemptKind.BOUNDARY


def _parse_github_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise GitHubApiError(f"GitHub returned an invalid {label} timestamp")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise GitHubApiError(
            f"GitHub returned an invalid {label} timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GitHubApiError(
            f"GitHub returned a timezone-free {label} timestamp"
        )
    return parsed.astimezone(timezone.utc)


def _run_created_at(run: Mapping[str, object]) -> datetime:
    value = run.get("created_at")
    if value is None:
        raise GitHubApiError("workflow history is missing created_at")
    return _parse_github_timestamp(value, label="workflow run created_at")


def _classify_history_attempt(
    jobs: Sequence[Mapping[str, object]],
    *,
    attempt_number: int,
    next_higher_run_created_at: datetime,
    attempt_job_name: str,
    gate_job_name: str,
    success_marker: str,
    ignored_marker: str,
) -> AttemptKind:
    attempt_job = _find_attempt_job(jobs, attempt_job_name)
    if attempt_job is not None:
        if attempt_job.get("status") != "completed":
            return AttemptKind.IGNORED
        started_at = attempt_job.get("started_at")
        if started_at is None:
            raise GitHubApiError("completed attempt job is missing started_at")
        # An original attempt may start after a newer run is queued while its
        # runner is offline. Only a rerun can be an out-of-order observation.
        if (
            attempt_number > 1
            and _parse_github_timestamp(
                started_at,
                label="attempt job started_at",
            )
            > next_higher_run_created_at
        ):
            return AttemptKind.IGNORED
        return classify_attempt(
            attempt_job,
            success_marker=success_marker,
            ignored_marker=ignored_marker,
        )

    kind = classify_attempt_jobs(
        jobs,
        attempt_job_name=attempt_job_name,
        gate_job_name=gate_job_name,
        success_marker=success_marker,
        ignored_marker=ignored_marker,
    )
    if kind is not AttemptKind.BOUNDARY:
        return kind

    started_at_values: list[datetime] = []
    for job in jobs:
        started_at = job.get("started_at")
        if started_at is None:
            raise GitHubApiError("legacy workflow job is missing started_at")
        started_at_values.append(
            _parse_github_timestamp(
                started_at,
                label="legacy workflow job started_at",
            )
        )
    if (
        attempt_number > 1
        and min(started_at_values) > next_higher_run_created_at
    ):
        return AttemptKind.IGNORED
    return kind


def evaluate_failure_streak(
    client: GitHubActionsClient,
    *,
    current_run_id: int,
    current_run_number: int,
    current_run_attempt: int,
    attempt_job_name: str,
    gate_job_name: str,
    success_marker: str = DEFAULT_SUCCESS_MARKER,
    ignored_marker: str = DEFAULT_IGNORED_MARKER,
    threshold: int = DEFAULT_THRESHOLD,
) -> GateDecision:
    if threshold < 1:
        raise ValueError("threshold must be positive")

    current_run = client.get_run(current_run_id)
    reported_run_number = current_run.get("run_number")
    if reported_run_number != current_run_number:
        raise GitHubApiError(
            "current run number does not match GITHUB_RUN_NUMBER"
        )
    reported_run_attempt = current_run.get("run_attempt")
    if reported_run_attempt != current_run_attempt:
        raise GitHubApiError(
            "current run attempt does not match GITHUB_RUN_ATTEMPT"
        )
    workflow_id = current_run.get("workflow_id")
    if not isinstance(workflow_id, int) or workflow_id < 1:
        raise GitHubApiError("current run is missing a valid workflow_id")
    head_branch = current_run.get("head_branch")
    if not isinstance(head_branch, str) or not head_branch:
        raise GitHubApiError("current run is missing a valid head_branch")

    current_kind = classify_attempt_jobs(
        client.get_jobs(current_run_id),
        attempt_job_name=attempt_job_name,
        gate_job_name=gate_job_name,
        success_marker=success_marker,
        ignored_marker=ignored_marker,
    )
    if (
        current_kind is not AttemptKind.FAILURE
        and current_run_attempt == 1
    ):
        return GateDecision(
            current_kind=current_kind,
            consecutive_failures=0,
            threshold=threshold,
        )

    current_run_created_at = _run_created_at(current_run)
    page_iterator = iter(
        client.iter_workflow_run_pages(workflow_id, head_branch)
    )
    try:
        first_page = next(page_iterator)
    except StopIteration:
        first_page = []

    def validated_page_runs(
        page: Sequence[Mapping[str, object]],
    ) -> list[tuple[int, Mapping[str, object]]]:
        page_runs: list[tuple[int, Mapping[str, object]]] = []
        for run in page:
            run_number = run.get("run_number")
            run_id = run.get("id")
            run_branch = run.get("head_branch")
            run_attempt = run.get("run_attempt")
            if not isinstance(run_number, int) or not isinstance(run_id, int):
                raise GitHubApiError(
                    "workflow history contains an invalid run id or run number"
                )
            if not isinstance(run_attempt, int) or run_attempt < 1:
                raise GitHubApiError(
                    "workflow history contains an invalid run attempt"
                )
            if not isinstance(run_branch, str) or not run_branch:
                raise GitHubApiError(
                    "workflow history contains an invalid head branch"
                )
            if run_branch == head_branch:
                page_runs.append((run_number, run))
        page_runs.sort(key=lambda item: item[0], reverse=True)
        return page_runs

    first_page_runs = validated_page_runs(first_page)
    if (
        current_run_attempt > 1
        and any(
            run_number > current_run_number
            for run_number, _run in first_page_runs
        )
    ):
        # A rerun of an older workflow run is not a new lane observation.
        # Ignoring it preserves run-number order without scanning arbitrary
        # historical attempts to reconstruct their wall-clock completion order.
        return GateDecision(
            current_kind=AttemptKind.IGNORED,
            consecutive_failures=0,
            threshold=threshold,
        )

    if current_kind is not AttemptKind.FAILURE:
        return GateDecision(
            current_kind=current_kind,
            consecutive_failures=0,
            threshold=threshold,
        )

    failures = 1

    def consume_history_attempt(attempt: AttemptKind) -> GateDecision | None:
        nonlocal failures
        if attempt is AttemptKind.FAILURE:
            failures += 1
            if failures >= threshold:
                return GateDecision(
                    current_kind=current_kind,
                    consecutive_failures=failures,
                    threshold=threshold,
                )
            return None
        if attempt is AttemptKind.IGNORED:
            return None
        return GateDecision(
            current_kind=current_kind,
            consecutive_failures=failures,
            threshold=threshold,
        )

    for attempt_number in range(current_run_attempt - 1, 0, -1):
        decision = consume_history_attempt(
            classify_attempt_jobs(
                client.get_attempt_jobs(current_run_id, attempt_number),
                attempt_job_name=attempt_job_name,
                gate_job_name=gate_job_name,
                success_marker=success_marker,
                ignored_marker=ignored_marker,
            )
        )
        if decision is not None:
            return decision

    last_run_number = current_run_number
    seen_run_numbers: set[int] = {current_run_number}
    next_higher_run_created_at = current_run_created_at
    all_pages = chain(
        [first_page_runs],
        map(validated_page_runs, page_iterator),
    )
    for previous_runs in all_pages:
        for run_number, run in previous_runs:
            if run_number >= current_run_number:
                continue
            if run_number in seen_run_numbers:
                continue
            if run_number >= last_run_number:
                raise GitHubApiError(
                    "workflow history is not ordered by descending run number"
                )
            seen_run_numbers.add(run_number)
            last_run_number = run_number
            run_id = run["id"]
            run_attempt = run["run_attempt"]
            assert isinstance(run_id, int)
            assert isinstance(run_attempt, int)
            run_created_at = _run_created_at(run)
            if run_created_at > next_higher_run_created_at:
                raise GitHubApiError(
                    "workflow run created_at is later than the next higher "
                    "run number"
                )
            for attempt_number in range(run_attempt, 0, -1):
                decision = consume_history_attempt(
                    _classify_history_attempt(
                        client.get_attempt_jobs(run_id, attempt_number),
                        attempt_number=attempt_number,
                        next_higher_run_created_at=(
                            next_higher_run_created_at
                        ),
                        attempt_job_name=attempt_job_name,
                        gate_job_name=gate_job_name,
                        success_marker=success_marker,
                        ignored_marker=ignored_marker,
                    )
                )
                if decision is not None:
                    return decision
            next_higher_run_created_at = run_created_at

    return GateDecision(
        current_kind=current_kind,
        consecutive_failures=failures,
        threshold=threshold,
    )


def _workflow_command_value(value: str) -> str:
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
        .replace(":", "%3A")
        .replace(",", "%2C")
    )


def _write_summary(
    summary_path: str | None,
    *,
    lane_name: str,
    message: str,
) -> None:
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8", newline="\n") as summary:
        summary.write(f"## {lane_name} failure streak\n\n{message}\n")


def _positive_int(raw: str, label: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise GitHubApiError(f"{label} must be an integer") from exc
    if value < 1:
        raise GitHubApiError(f"{label} must be positive")
    return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enforce a lane-level GitHub Actions failure streak",
    )
    parser.add_argument(
        "--api-url",
        default=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    parser.add_argument(
        "--repository",
        default=os.environ.get("GITHUB_REPOSITORY", ""),
    )
    parser.add_argument(
        "--run-id",
        default=os.environ.get("GITHUB_RUN_ID", ""),
    )
    parser.add_argument(
        "--run-number",
        default=os.environ.get("GITHUB_RUN_NUMBER", ""),
    )
    parser.add_argument(
        "--run-attempt",
        default=os.environ.get("GITHUB_RUN_ATTEMPT", ""),
    )
    parser.add_argument(
        "--attempt-job",
        default=os.environ.get("TC_FAILURE_STREAK_ATTEMPT_JOB", ""),
    )
    parser.add_argument(
        "--gate-job",
        default=os.environ.get("TC_FAILURE_STREAK_GATE_JOB", ""),
    )
    parser.add_argument(
        "--lane",
        default=os.environ.get("TC_FAILURE_STREAK_LANE", ""),
    )
    parser.add_argument(
        "--success-marker",
        default=DEFAULT_SUCCESS_MARKER,
    )
    parser.add_argument(
        "--ignored-marker",
        default=DEFAULT_IGNORED_MARKER,
    )
    parser.add_argument(
        "--threshold",
        default=str(DEFAULT_THRESHOLD),
    )
    return parser


def run_gate(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if not args.attempt_job:
            raise GitHubApiError("attempt job name is required")
        if not args.gate_job:
            raise GitHubApiError("gate job name is required")
        if not args.lane:
            raise GitHubApiError("lane name is required")
        client = GitHubActionsClient(
            api_url=args.api_url,
            repository=args.repository,
            token=os.environ.get("GITHUB_TOKEN", ""),
        )
        decision = evaluate_failure_streak(
            client,
            current_run_id=_positive_int(args.run_id, "GITHUB_RUN_ID"),
            current_run_number=_positive_int(
                args.run_number,
                "GITHUB_RUN_NUMBER",
            ),
            current_run_attempt=_positive_int(
                args.run_attempt,
                "GITHUB_RUN_ATTEMPT",
            ),
            attempt_job_name=args.attempt_job,
            gate_job_name=args.gate_job,
            success_marker=args.success_marker,
            ignored_marker=args.ignored_marker,
            threshold=_positive_int(args.threshold, "threshold"),
        )
    except (GitHubApiError, OSError) as exc:
        message = f"Failure streak could not be evaluated safely: {exc}"
        print(
            "::error title=Failure streak gate error::"
            + _workflow_command_value(message)
        )
        _write_summary(
            os.environ.get("GITHUB_STEP_SUMMARY"),
            lane_name=args.lane or "Collection lane",
            message=message,
        )
        return 2

    if decision.current_kind is AttemptKind.SUCCESS:
        message = "The current collection attempt succeeded; the streak is reset."
        print(message)
        _write_summary(
            os.environ.get("GITHUB_STEP_SUMMARY"),
            lane_name=args.lane,
            message=message,
        )
        return 0
    if decision.current_kind is AttemptKind.IGNORED:
        message = (
            "The current run was intentionally ignored; it neither increments "
            "nor resets the streak."
        )
        print(message)
        _write_summary(
            os.environ.get("GITHUB_STEP_SUMMARY"),
            lane_name=args.lane,
            message=message,
        )
        return 0
    if decision.current_kind is AttemptKind.BOUNDARY:
        message = (
            "The current run reached a legacy boundary and was not counted."
        )
        print(message)
        _write_summary(
            os.environ.get("GITHUB_STEP_SUMMARY"),
            lane_name=args.lane,
            message=message,
        )
        return 0

    streak = decision.consecutive_failures
    if decision.should_fail:
        message = (
            f"{args.lane} failed at least {streak} consecutive counted attempts "
            f"(threshold: {decision.threshold})."
        )
        print(
            "::error title=Consecutive collection failures::"
            + _workflow_command_value(message)
        )
        _write_summary(
            os.environ.get("GITHUB_STEP_SUMMARY"),
            lane_name=args.lane,
            message=message,
        )
        return 1

    message = (
        f"{args.lane} raw failure {streak}/{decision.threshold}; "
        "the workflow remains successful until the threshold is reached."
    )
    print(
        "::warning title=Collection attempt failed::"
        + _workflow_command_value(message)
    )
    _write_summary(
        os.environ.get("GITHUB_STEP_SUMMARY"),
        lane_name=args.lane,
        message=message,
    )
    return 0


def main() -> None:
    raise SystemExit(run_gate())


if __name__ == "__main__":
    main()
