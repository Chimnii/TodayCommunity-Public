import assert from "node:assert/strict";
import test from "node:test";

import {
  GITHUB_API_VERSION,
  SCHEDULES,
  default as worker,
  decideDispatch,
  dispatchScheduledWorkflow,
  workflowForCron,
} from "../scheduler/src/index.mjs";

const NOW = Date.parse("2026-07-20T00:20:00Z");
const ENV = Object.freeze({
  GITHUB_DISPATCH_TOKEN: "test-token",
  GITHUB_OWNER: "Chimnii",
  GITHUB_REPOSITORY: "TodayCommunity-Public",
  GITHUB_REF: "main",
});

function jsonResponse(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function noContentResponse() {
  return new Response(null, { status: 204 });
}

test("cron strings map to the intended workflows", () => {
  assert.deepEqual(workflowForCron("7,22,37,52 * * * *"), {
    kind: "hot",
    workflow: "scan-dcinside.yml",
  });
  assert.deepEqual(workflowForCron("56 */6 * * *"), {
    kind: "backfill",
    workflow: "scan-dcinside-backfill.yml",
  });
  assert.throws(
    () => workflowForCron("0 * * * *"),
    /Unsupported scheduler cron/,
  );
});

test("Worker exposes only the scheduled handler", () => {
  assert.deepEqual(Object.keys(worker).sort(), ["scheduled"]);
  assert.equal(worker.fetch, undefined);
});

test("an eligible Hot cron dispatches the workflow with hardened GitHub headers", async () => {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return calls.length === 1
      ? jsonResponse({ workflow_runs: [] })
      : noContentResponse();
  };

  const result = await dispatchScheduledWorkflow({
    cron: "7,22,37,52 * * * *",
    env: ENV,
    fetchImpl,
    now: () => NOW,
  });

  assert.deepEqual(result, {
    status: "dispatched",
    kind: "hot",
    workflow: "scan-dcinside.yml",
    ref: "main",
  });
  assert.equal(calls.length, 2);
  assert.match(calls[0].url, /\/actions\/runs\?per_page=100$/);
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[1].options.method, "POST");
  assert.match(
    calls[1].url,
    /\/actions\/workflows\/scan-dcinside\.yml\/dispatches$/,
  );
  assert.equal(calls[1].options.headers.Accept, "application/vnd.github+json");
  assert.equal(
    calls[1].options.headers["X-GitHub-Api-Version"],
    GITHUB_API_VERSION,
  );
  assert.equal(
    calls[1].options.headers["User-Agent"],
    "TodayCommunity-Cloudflare-Scheduler",
  );
  assert.equal(calls[1].options.headers.Authorization, "Bearer test-token");
  assert.deepEqual(JSON.parse(calls[1].options.body), { ref: "main" });
});

test("a recently completed dispatch of the same workflow suppresses a duplicate", async () => {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return jsonResponse({
      workflow_runs: [
        {
          id: 101,
          event: "workflow_dispatch",
          status: "completed",
          head_branch: "main",
          path: ".github/workflows/scan-dcinside.yml",
          created_at: "2026-07-20T00:15:00Z",
        },
      ],
    });
  };

  const result = await dispatchScheduledWorkflow({
    cron: "7,22,37,52 * * * *",
    env: ENV,
    fetchImpl,
    now: () => NOW,
  });

  assert.equal(calls.length, 1);
  assert.deepEqual(result, {
    status: "skipped",
    kind: "hot",
    workflow: "scan-dcinside.yml",
    reason: "recent_same_workflow_dispatch",
    runId: 101,
  });
});

test("Hot skips while another managed workflow is active", () => {
  const decision = decideDispatch({
    runs: [
      {
        id: 202,
        event: "schedule",
        status: "in_progress",
        path: ".github/workflows/scan-dcinside-backfill.yml",
      },
    ],
    schedule: SCHEDULES["7,22,37,52 * * * *"],
    ref: "main",
    nowMs: NOW,
  });

  assert.deepEqual(decision, {
    action: "skip",
    reason: "managed_workflow_active",
    runId: 202,
  });
});

test("Backfill may queue behind Hot but skips another active Backfill", () => {
  const schedule = SCHEDULES["56 */6 * * *"];
  assert.deepEqual(
    decideDispatch({
      runs: [
        {
          id: 301,
          event: "schedule",
          status: "queued",
          path: ".github/workflows/scan-dcinside.yml",
        },
      ],
      schedule,
      ref: "main",
      nowMs: NOW,
    }),
    { action: "dispatch" },
  );

  assert.deepEqual(
    decideDispatch({
      runs: [
        {
          id: 302,
          event: "schedule",
          status: "queued",
          path: ".github/workflows/scan-dcinside-backfill.yml",
        },
      ],
      schedule,
      ref: "main",
      nowMs: NOW,
    }),
    { action: "skip", reason: "backfill_active", runId: 302 },
  );
});

test("a failed GitHub dispatch response is surfaced as an error", async () => {
  let callCount = 0;
  const fetchImpl = async () => {
    callCount += 1;
    return callCount === 1
      ? jsonResponse({ workflow_runs: [] })
      : jsonResponse({ message: "Forbidden" }, 403);
  };

  await assert.rejects(
    dispatchScheduledWorkflow({
      cron: "56 */6 * * *",
      env: ENV,
      fetchImpl,
      now: () => NOW,
    }),
    /GitHub API POST .* failed with HTTP 403.*Forbidden/,
  );
});
