from __future__ import annotations

import io
import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib import error, parse

from crawler.jobs.enforce_failure_streak import (
    DEFAULT_IGNORED_MARKER,
    DEFAULT_SUCCESS_MARKER,
    AttemptKind,
    GitHubActionsClient,
    GitHubApiError,
    classify_attempt,
    classify_attempt_jobs,
    count_consecutive_failures,
    evaluate_failure_streak,
)


ATTEMPT_JOB_NAME = "DC Hot crawl attempt"
GATE_JOB_NAME = "DC Hot failure streak gate"
FAKE_TIME_ORIGIN = datetime(2026, 1, 1, tzinfo=timezone.utc)


def fake_timestamp(
    run_number: int,
    attempt_number: int = 0,
    *,
    offset_seconds: int = 0,
) -> str:
    value = FAKE_TIME_ORIGIN + timedelta(
        seconds=(run_number * 100) + attempt_number + offset_seconds,
    )
    return value.isoformat().replace("+00:00", "Z")


def with_started_at(
    job: dict[str, object],
    started_at: str,
) -> dict[str, object]:
    return {**job, "started_at": started_at}


def step(name: str, conclusion: str) -> dict[str, object]:
    return {
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
    }


def attempt_job(
    conclusion: str,
    *,
    steps: list[dict[str, object]] | None = None,
    name: str = ATTEMPT_JOB_NAME,
) -> dict[str, object]:
    return {
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "steps": steps or [],
    }


def successful_attempt() -> dict[str, object]:
    return attempt_job(
        "success",
        steps=[step(DEFAULT_SUCCESS_MARKER, "success")],
    )


def failed_attempt() -> dict[str, object]:
    return attempt_job(
        "failure",
        steps=[
            step("Run collection", "failure"),
            step(DEFAULT_SUCCESS_MARKER, "skipped"),
        ],
    )


def ignored_attempt() -> dict[str, object]:
    return attempt_job(
        "success",
        steps=[
            step(DEFAULT_IGNORED_MARKER, "success"),
            step(DEFAULT_SUCCESS_MARKER, "skipped"),
        ],
    )


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class AttemptClassificationTests(unittest.TestCase):
    def test_success_requires_the_counted_success_marker(self) -> None:
        self.assertEqual(
            classify_attempt(successful_attempt()),
            AttemptKind.SUCCESS,
        )

    def test_failure_uses_raw_step_or_job_failure(self) -> None:
        self.assertEqual(
            classify_attempt(failed_attempt()),
            AttemptKind.FAILURE,
        )
        job_level_continue_on_error = attempt_job(
            "success",
            steps=[
                step("Run collection", "failure"),
                step(DEFAULT_SUCCESS_MARKER, "skipped"),
            ],
        )
        self.assertEqual(
            classify_attempt(job_level_continue_on_error),
            AttemptKind.FAILURE,
        )

    def test_stale_and_non_main_runs_are_ignored(self) -> None:
        self.assertEqual(
            classify_attempt(ignored_attempt()),
            AttemptKind.IGNORED,
        )
        self.assertEqual(
            classify_attempt(attempt_job("skipped")),
            AttemptKind.IGNORED,
        )

    def test_legacy_job_is_a_safe_boundary(self) -> None:
        self.assertEqual(
            classify_attempt(attempt_job("success")),
            AttemptKind.BOUNDARY,
        )
        self.assertEqual(classify_attempt(None), AttemptKind.BOUNDARY)

    def test_missing_attempt_distinguishes_ignored_from_legacy_jobs(self) -> None:
        self.assertEqual(
            classify_attempt_jobs(
                [],
                attempt_job_name=ATTEMPT_JOB_NAME,
                gate_job_name=GATE_JOB_NAME,
            ),
            AttemptKind.IGNORED,
        )
        self.assertEqual(
            classify_attempt_jobs(
                [
                    {
                        "name": GATE_JOB_NAME,
                        "status": "in_progress",
                        "conclusion": None,
                        "steps": [],
                    }
                ],
                attempt_job_name=ATTEMPT_JOB_NAME,
                gate_job_name=GATE_JOB_NAME,
            ),
            AttemptKind.IGNORED,
        )
        self.assertEqual(
            classify_attempt_jobs(
                [attempt_job("success", name="legacy hot job")],
                attempt_job_name=ATTEMPT_JOB_NAME,
                gate_job_name=GATE_JOB_NAME,
            ),
            AttemptKind.BOUNDARY,
        )


class ConsecutiveFailureCountTests(unittest.TestCase):
    def test_failure_one_two_three_and_four(self) -> None:
        for expected in range(1, 5):
            with self.subTest(expected=expected):
                attempts = [AttemptKind.FAILURE] * expected
                self.assertEqual(
                    count_consecutive_failures(attempts),
                    expected,
                )

    def test_success_resets_the_older_failure_history(self) -> None:
        self.assertEqual(
            count_consecutive_failures(
                [
                    AttemptKind.FAILURE,
                    AttemptKind.SUCCESS,
                    AttemptKind.FAILURE,
                    AttemptKind.FAILURE,
                ]
            ),
            1,
        )
        self.assertEqual(
            count_consecutive_failures(
                [AttemptKind.SUCCESS, AttemptKind.FAILURE]
            ),
            0,
        )

    def test_ignored_runs_do_not_increment_or_reset(self) -> None:
        self.assertEqual(
            count_consecutive_failures(
                [
                    AttemptKind.FAILURE,
                    AttemptKind.IGNORED,
                    AttemptKind.FAILURE,
                    AttemptKind.IGNORED,
                    AttemptKind.FAILURE,
                ]
            ),
            3,
        )

    def test_legacy_boundary_stops_the_streak(self) -> None:
        self.assertEqual(
            count_consecutive_failures(
                [
                    AttemptKind.FAILURE,
                    AttemptKind.BOUNDARY,
                    AttemptKind.FAILURE,
                ]
            ),
            1,
        )


class GitHubActionsClientTests(unittest.TestCase):
    def test_jobs_endpoint_uses_latest_filter_and_paginates(self) -> None:
        calls: list[str] = []
        first_page = [attempt_job("success", name=f"job-{i}") for i in range(100)]
        second_page = [attempt_job("success", name="job-100")]

        def opener(api_request, *, timeout):
            del timeout
            calls.append(api_request.full_url)
            query = parse.parse_qs(parse.urlsplit(api_request.full_url).query)
            page_number = int(query["page"][0])
            payload = {
                "total_count": 101,
                "jobs": first_page if page_number == 1 else second_page,
            }
            return FakeResponse(payload)

        client = GitHubActionsClient(
            api_url="https://api.github.test",
            repository="owner/repository",
            token="secret-token",
            opener=opener,
        )

        jobs = client.get_jobs(42)

        self.assertEqual(len(jobs), 101)
        self.assertEqual(len(calls), 2)
        for page_number, url in enumerate(calls, start=1):
            query = parse.parse_qs(parse.urlsplit(url).query)
            self.assertEqual(query["filter"], ["latest"])
            self.assertEqual(query["per_page"], ["100"])
            self.assertEqual(query["page"], [str(page_number)])
            self.assertNotIn("secret-token", url)

    def test_previous_attempt_jobs_use_the_attempt_specific_endpoint(self) -> None:
        calls: list[str] = []

        def opener(api_request, *, timeout):
            del timeout
            calls.append(api_request.full_url)
            return FakeResponse(
                {
                    "total_count": 1,
                    "jobs": [failed_attempt()],
                }
            )

        client = GitHubActionsClient(
            api_url="https://api.github.test",
            repository="owner/repository",
            token="secret-token",
            opener=opener,
        )

        jobs = client.get_attempt_jobs(42, 3)

        self.assertEqual(jobs, [failed_attempt()])
        self.assertEqual(len(calls), 1)
        split = parse.urlsplit(calls[0])
        self.assertEqual(
            split.path,
            "/repos/owner/repository/actions/runs/42/attempts/3/jobs",
        )
        query = parse.parse_qs(split.query)
        self.assertNotIn("filter", query)
        self.assertEqual(query["per_page"], ["100"])
        self.assertEqual(query["page"], ["1"])

    def test_completed_workflow_history_paginates_by_page(self) -> None:
        calls: list[str] = []
        first_page = [
            {"id": run_number, "run_number": run_number}
            for run_number in range(200, 100, -1)
        ]
        second_page = [{"id": 100, "run_number": 100}]

        def opener(api_request, *, timeout):
            del timeout
            calls.append(api_request.full_url)
            query = parse.parse_qs(parse.urlsplit(api_request.full_url).query)
            page_number = int(query["page"][0])
            payload = {
                "total_count": 101,
                "workflow_runs": (
                    first_page if page_number == 1 else second_page
                ),
            }
            return FakeResponse(payload)

        client = GitHubActionsClient(
            api_url="https://api.github.test",
            repository="owner/repository",
            token="secret-token",
            opener=opener,
        )

        pages = list(client.iter_workflow_run_pages(7, "main"))

        self.assertEqual([len(page) for page in pages], [100, 1])
        self.assertEqual(len(calls), 2)
        for page_number, url in enumerate(calls, start=1):
            query = parse.parse_qs(parse.urlsplit(url).query)
            self.assertEqual(query["branch"], ["main"])
            self.assertNotIn("status", query)
            self.assertEqual(query["per_page"], ["100"])
            self.assertEqual(query["page"], [str(page_number)])

    def test_http_error_is_clear_and_never_contains_the_token(self) -> None:
        def opener(api_request, *, timeout):
            del timeout
            raise error.HTTPError(
                api_request.full_url,
                403,
                "Forbidden",
                {},
                io.BytesIO(b'{"message":"forbidden"}'),
            )

        client = GitHubActionsClient(
            api_url="https://api.github.test",
            repository="owner/repository",
            token="do-not-print-this-token",
            opener=opener,
        )

        with self.assertRaisesRegex(GitHubApiError, "HTTP 403") as raised:
            client.get_jobs(42)
        self.assertNotIn("do-not-print-this-token", str(raised.exception))


class FakeActionsClient:
    def __init__(
        self,
        jobs_by_run: dict[int, list[dict[str, object]]],
        previous_run_pages: list[list[dict[str, object]]],
        *,
        attempt_jobs: (
            dict[tuple[int, int], list[dict[str, object]]] | None
        ) = None,
        run_attempts: dict[int, int] | None = None,
    ) -> None:
        self.jobs_by_run = jobs_by_run
        self.attempt_jobs = (
            attempt_jobs
            if attempt_jobs is not None
            else {
                (run_id, 1): jobs
                for run_id, jobs in jobs_by_run.items()
            }
        )
        self.run_attempts = (
            run_attempts
            if run_attempts is not None
            else {run_id: 1 for run_id in jobs_by_run}
        )
        self.previous_run_pages = previous_run_pages
        self.latest_job_requests: list[int] = []
        self.attempt_job_requests: list[tuple[int, int]] = []

    def _timestamped_jobs(
        self,
        jobs: list[dict[str, object]],
        *,
        run_id: int,
        attempt_number: int,
    ) -> list[dict[str, object]]:
        default_started_at = fake_timestamp(run_id, attempt_number)
        return [
            (
                job
                if "started_at" in job
                else {**job, "started_at": default_started_at}
            )
            for job in jobs
        ]

    def get_jobs(self, run_id: int) -> list[dict[str, object]]:
        self.latest_job_requests.append(run_id)
        return self._timestamped_jobs(
            self.jobs_by_run[run_id],
            run_id=run_id,
            attempt_number=self.run_attempts[run_id],
        )

    def get_attempt_jobs(
        self,
        run_id: int,
        attempt_number: int,
    ) -> list[dict[str, object]]:
        self.attempt_job_requests.append((run_id, attempt_number))
        return self._timestamped_jobs(
            self.attempt_jobs[(run_id, attempt_number)],
            run_id=run_id,
            attempt_number=attempt_number,
        )

    def get_run(self, run_id: int) -> dict[str, object]:
        return {
            "id": run_id,
            "run_number": run_id,
            "run_attempt": self.run_attempts[run_id],
            "workflow_id": 7,
            "head_branch": "main",
            "created_at": fake_timestamp(run_id),
        }

    def iter_workflow_run_pages(self, workflow_id: int, branch: str):
        if workflow_id != 7:
            raise AssertionError("unexpected workflow id")
        if branch != "main":
            raise AssertionError("unexpected branch")
        for page in self.previous_run_pages:
            yield [
                (
                    run
                    if "created_at" in run
                    else {
                        **run,
                        "created_at": fake_timestamp(
                            int(run["run_number"]),
                        ),
                    }
                )
                for run in page
            ]


class FailureStreakEvaluationTests(unittest.TestCase):
    def test_threshold_applies_to_each_counted_failure(self) -> None:
        for failure_count in range(1, 5):
            with self.subTest(failure_count=failure_count):
                current_run = 100 + failure_count
                failed_runs = list(
                    range(current_run, current_run - failure_count, -1)
                )
                reset_run = current_run - failure_count
                jobs_by_run = {
                    run_id: [failed_attempt()] for run_id in failed_runs
                }
                jobs_by_run[reset_run] = [successful_attempt()]
                previous_runs = [
                    {
                        "id": run_id,
                        "run_number": run_id,
                        "run_attempt": 1,
                        "head_branch": "main",
                    }
                    for run_id in failed_runs[1:]
                ]
                previous_runs.append(
                    {
                        "id": reset_run,
                        "run_number": reset_run,
                        "run_attempt": 1,
                        "head_branch": "main",
                    }
                )
                client = FakeActionsClient(
                    jobs_by_run=jobs_by_run,
                    previous_run_pages=[previous_runs],
                )

                decision = evaluate_failure_streak(
                    client,  # type: ignore[arg-type]
                    current_run_id=current_run,
                    current_run_number=current_run,
                    current_run_attempt=1,
                    attempt_job_name=ATTEMPT_JOB_NAME,
                    gate_job_name=GATE_JOB_NAME,
                )

                self.assertEqual(
                    decision.should_fail,
                    failure_count >= 3,
                )
                self.assertEqual(
                    decision.consecutive_failures,
                    min(failure_count, 3),
                )

    def test_three_failures_in_three_attempts_of_one_run_reach_threshold(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={103: [failed_attempt()]},
            previous_run_pages=[],
            attempt_jobs={
                (103, 2): [failed_attempt()],
                (103, 1): [failed_attempt()],
            },
            run_attempts={103: 3},
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=103,
            current_run_number=103,
            current_run_attempt=3,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertTrue(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 3)
        self.assertEqual(client.latest_job_requests, [103])
        self.assertEqual(
            client.attempt_job_requests,
            [(103, 2), (103, 1)],
        )

    def test_successful_rerun_resets_without_reading_older_attempts(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={103: [successful_attempt()]},
            previous_run_pages=[],
            attempt_jobs={
                (103, 2): [failed_attempt()],
                (103, 1): [failed_attempt()],
            },
            run_attempts={103: 3},
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=103,
            current_run_number=103,
            current_run_attempt=3,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertEqual(decision.current_kind, AttemptKind.SUCCESS)
        self.assertEqual(decision.consecutive_failures, 0)
        self.assertEqual(client.latest_job_requests, [103])
        self.assertEqual(client.attempt_job_requests, [])

    def test_latest_successful_attempt_of_previous_run_resets_streak(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={104: [failed_attempt()]},
            previous_run_pages=[
                [
                    {
                        "id": 103,
                        "run_number": 103,
                        "run_attempt": 2,
                        "head_branch": "main",
                    }
                ]
            ],
            attempt_jobs={
                (103, 2): [successful_attempt()],
                (103, 1): [failed_attempt()],
            },
            run_attempts={104: 1},
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=104,
            current_run_number=104,
            current_run_attempt=1,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertFalse(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 1)
        self.assertEqual(client.attempt_job_requests, [(103, 2)])

    def test_late_successful_rerun_of_older_run_is_ignored(self) -> None:
        run_103_created_at = fake_timestamp(103)
        client = FakeActionsClient(
            jobs_by_run={104: [failed_attempt()]},
            previous_run_pages=[
                [
                    {
                        "id": 103,
                        "run_number": 103,
                        "run_attempt": 1,
                        "head_branch": "main",
                        "created_at": run_103_created_at,
                    },
                    {
                        "id": 102,
                        "run_number": 102,
                        "run_attempt": 2,
                        "head_branch": "main",
                    },
                ]
            ],
            attempt_jobs={
                (103, 1): [failed_attempt()],
                (102, 2): [
                    with_started_at(
                        successful_attempt(),
                        fake_timestamp(103, offset_seconds=10),
                    )
                ],
                (102, 1): [failed_attempt()],
            },
            run_attempts={104: 1},
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=104,
            current_run_number=104,
            current_run_attempt=1,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertTrue(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 3)
        self.assertEqual(
            client.attempt_job_requests,
            [(103, 1), (102, 2), (102, 1)],
        )

    def test_late_failed_rerun_of_older_run_is_ignored(self) -> None:
        run_103_created_at = fake_timestamp(103)
        client = FakeActionsClient(
            jobs_by_run={104: [failed_attempt()]},
            previous_run_pages=[
                [
                    {
                        "id": 103,
                        "run_number": 103,
                        "run_attempt": 1,
                        "head_branch": "main",
                        "created_at": run_103_created_at,
                    },
                    {
                        "id": 102,
                        "run_number": 102,
                        "run_attempt": 2,
                        "head_branch": "main",
                    },
                ]
            ],
            attempt_jobs={
                (103, 1): [failed_attempt()],
                (102, 2): [
                    with_started_at(
                        failed_attempt(),
                        fake_timestamp(103, offset_seconds=10),
                    )
                ],
                (102, 1): [successful_attempt()],
            },
            run_attempts={104: 1},
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=104,
            current_run_number=104,
            current_run_attempt=1,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertFalse(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 2)
        self.assertEqual(
            client.attempt_job_requests,
            [(103, 1), (102, 2), (102, 1)],
        )

    def test_rerun_started_before_next_run_creation_is_counted(self) -> None:
        run_103_created_at = fake_timestamp(103)
        client = FakeActionsClient(
            jobs_by_run={104: [failed_attempt()]},
            previous_run_pages=[
                [
                    {
                        "id": 103,
                        "run_number": 103,
                        "run_attempt": 1,
                        "head_branch": "main",
                        "created_at": run_103_created_at,
                    },
                    {
                        "id": 102,
                        "run_number": 102,
                        "run_attempt": 2,
                        "head_branch": "main",
                    },
                ]
            ],
            attempt_jobs={
                (103, 1): [failed_attempt()],
                (102, 2): [
                    with_started_at(
                        failed_attempt(),
                        fake_timestamp(103, offset_seconds=-1),
                    )
                ],
                (102, 1): [successful_attempt()],
            },
            run_attempts={104: 1},
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=104,
            current_run_number=104,
            current_run_attempt=1,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertTrue(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 3)
        self.assertEqual(
            client.attempt_job_requests,
            [(103, 1), (102, 2)],
        )

    def test_delayed_original_attempt_is_counted_after_next_run_creation(
        self,
    ) -> None:
        client = FakeActionsClient(
            jobs_by_run={105: [failed_attempt()]},
            previous_run_pages=[
                [
                    {
                        "id": 104,
                        "run_number": 104,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                    {
                        "id": 103,
                        "run_number": 103,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                ]
            ],
            attempt_jobs={
                (104, 1): [failed_attempt()],
                (103, 1): [
                    with_started_at(
                        failed_attempt(),
                        fake_timestamp(104, offset_seconds=10),
                    )
                ],
            },
            run_attempts={105: 1},
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=105,
            current_run_number=105,
            current_run_attempt=1,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertTrue(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 3)
        self.assertEqual(
            client.attempt_job_requests,
            [(104, 1), (103, 1)],
        )

    def test_current_rerun_is_ignored_when_a_higher_run_exists(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={102: [failed_attempt()]},
            previous_run_pages=[
                [
                    {
                        "id": 104,
                        "run_number": 104,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                    {
                        "id": 103,
                        "run_number": 103,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                    {
                        "id": 102,
                        "run_number": 102,
                        "run_attempt": 2,
                        "head_branch": "main",
                    },
                ]
            ],
            run_attempts={102: 2},
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=102,
            current_run_number=102,
            current_run_attempt=2,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertEqual(decision.current_kind, AttemptKind.IGNORED)
        self.assertFalse(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 0)
        self.assertEqual(client.latest_job_requests, [102])
        self.assertEqual(client.attempt_job_requests, [])

    def test_late_legacy_rerun_is_ignored_before_original_boundary(self) -> None:
        run_103_created_at = fake_timestamp(103)
        client = FakeActionsClient(
            jobs_by_run={104: [failed_attempt()]},
            previous_run_pages=[
                [
                    {
                        "id": 103,
                        "run_number": 103,
                        "run_attempt": 1,
                        "head_branch": "main",
                        "created_at": run_103_created_at,
                    },
                    {
                        "id": 102,
                        "run_number": 102,
                        "run_attempt": 2,
                        "head_branch": "main",
                    },
                ]
            ],
            attempt_jobs={
                (103, 1): [failed_attempt()],
                (102, 2): [
                    with_started_at(
                        attempt_job("success", name="legacy hot job"),
                        fake_timestamp(103, offset_seconds=10),
                    )
                ],
                (102, 1): [
                    with_started_at(
                        attempt_job("success", name="legacy hot job"),
                        fake_timestamp(102, 1),
                    )
                ],
            },
            run_attempts={104: 1},
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=104,
            current_run_number=104,
            current_run_attempt=1,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertFalse(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 2)
        self.assertEqual(
            client.attempt_job_requests,
            [(103, 1), (102, 2), (102, 1)],
        )

    def test_missing_history_started_at_fails_safe(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={104: [failed_attempt()]},
            previous_run_pages=[
                [
                    {
                        "id": 103,
                        "run_number": 103,
                        "run_attempt": 1,
                        "head_branch": "main",
                    }
                ]
            ],
            attempt_jobs={
                (103, 1): [{**failed_attempt(), "started_at": None}],
            },
            run_attempts={104: 1},
        )

        with self.assertRaisesRegex(
            GitHubApiError,
            "missing started_at",
        ):
            evaluate_failure_streak(
                client,  # type: ignore[arg-type]
                current_run_id=104,
                current_run_number=104,
                current_run_attempt=1,
                attempt_job_name=ATTEMPT_JOB_NAME,
                gate_job_name=GATE_JOB_NAME,
            )

    def test_run_creation_order_contradiction_fails_safe(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={104: [failed_attempt()]},
            previous_run_pages=[
                [
                    {
                        "id": 103,
                        "run_number": 103,
                        "run_attempt": 1,
                        "head_branch": "main",
                        "created_at": fake_timestamp(
                            104,
                            offset_seconds=10,
                        ),
                    }
                ]
            ],
            attempt_jobs={(103, 1): [failed_attempt()]},
            run_attempts={104: 1},
        )

        with self.assertRaisesRegex(
            GitHubApiError,
            "later than the next higher",
        ):
            evaluate_failure_streak(
                client,  # type: ignore[arg-type]
                current_run_id=104,
                current_run_number=104,
                current_run_attempt=1,
                attempt_job_name=ATTEMPT_JOB_NAME,
                gate_job_name=GATE_JOB_NAME,
            )

    def test_empty_cancelled_attempt_does_not_break_failures(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={103: [failed_attempt()]},
            previous_run_pages=[
                [
                    {
                        "id": 102,
                        "run_number": 102,
                        "run_attempt": 2,
                        "head_branch": "main",
                    },
                    {
                        "id": 101,
                        "run_number": 101,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                ]
            ],
            attempt_jobs={
                (102, 2): [],
                (102, 1): [failed_attempt()],
                (101, 1): [failed_attempt()],
            },
            run_attempts={103: 1},
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=103,
            current_run_number=103,
            current_run_attempt=1,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertTrue(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 3)
        self.assertEqual(
            client.attempt_job_requests,
            [(102, 2), (102, 1), (101, 1)],
        )

    def test_current_in_progress_run_is_read_directly_then_previous_runs(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={
                103: [failed_attempt()],
                102: [ignored_attempt()],
                101: [failed_attempt()],
                100: [failed_attempt()],
            },
            previous_run_pages=[
                [
                    {
                        "id": 103,
                        "run_number": 103,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                    {
                        "id": 102,
                        "run_number": 102,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                    {
                        "id": 101,
                        "run_number": 101,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                    {
                        "id": 100,
                        "run_number": 100,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                ]
            ],
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=103,
            current_run_number=103,
            current_run_attempt=1,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertTrue(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 3)
        self.assertEqual(client.latest_job_requests, [103])
        self.assertEqual(
            client.attempt_job_requests,
            [(102, 1), (101, 1), (100, 1)],
        )

    def test_success_resets_without_querying_previous_runs(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={103: [successful_attempt()]},
            previous_run_pages=[],
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=103,
            current_run_number=103,
            current_run_attempt=1,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertEqual(decision.current_kind, AttemptKind.SUCCESS)
        self.assertEqual(decision.consecutive_failures, 0)
        self.assertEqual(client.latest_job_requests, [103])
        self.assertEqual(client.attempt_job_requests, [])

    def test_current_run_attempt_must_match_github_context(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={103: [failed_attempt()]},
            previous_run_pages=[],
            run_attempts={103: 2},
        )

        with self.assertRaisesRegex(
            GitHubApiError,
            "GITHUB_RUN_ATTEMPT",
        ):
            evaluate_failure_streak(
                client,  # type: ignore[arg-type]
                current_run_id=103,
                current_run_number=103,
                current_run_attempt=1,
                attempt_job_name=ATTEMPT_JOB_NAME,
                gate_job_name=GATE_JOB_NAME,
            )

        self.assertEqual(client.latest_job_requests, [])

    def test_legacy_run_stops_history_without_counting_older_failures(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={
                103: [failed_attempt()],
                102: [attempt_job("success", name="legacy hot job")],
                101: [failed_attempt()],
            },
            previous_run_pages=[
                [
                    {
                        "id": 102,
                        "run_number": 102,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                    {
                        "id": 101,
                        "run_number": 101,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                ]
            ],
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=103,
            current_run_number=103,
            current_run_attempt=1,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertFalse(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 1)
        self.assertEqual(client.latest_job_requests, [103])
        self.assertEqual(client.attempt_job_requests, [(102, 1)])

    def test_other_branch_runs_never_affect_the_streak(self) -> None:
        client = FakeActionsClient(
            jobs_by_run={
                103: [failed_attempt()],
                101: [failed_attempt()],
                100: [successful_attempt()],
            },
            previous_run_pages=[
                [
                    {
                        "id": 102,
                        "run_number": 102,
                        "run_attempt": 1,
                        "head_branch": "feature",
                    },
                    {
                        "id": 101,
                        "run_number": 101,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                    {
                        "id": 100,
                        "run_number": 100,
                        "run_attempt": 1,
                        "head_branch": "main",
                    },
                ]
            ],
        )

        decision = evaluate_failure_streak(
            client,  # type: ignore[arg-type]
            current_run_id=103,
            current_run_number=103,
            current_run_attempt=1,
            attempt_job_name=ATTEMPT_JOB_NAME,
            gate_job_name=GATE_JOB_NAME,
        )

        self.assertFalse(decision.should_fail)
        self.assertEqual(decision.consecutive_failures, 2)
        self.assertEqual(client.latest_job_requests, [103])
        self.assertEqual(
            client.attempt_job_requests,
            [(101, 1), (100, 1)],
        )


if __name__ == "__main__":
    unittest.main()
