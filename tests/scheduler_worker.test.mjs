import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  GITHUB_API_VERSION,
  SCHEDULES,
  ScheduledDispatchError,
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
const WRANGLER_CONFIG = JSON.parse(
  await readFile(new URL("../scheduler/wrangler.jsonc", import.meta.url), "utf8")
);

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
    destinations: ["dcinside", "fmkorea"],
  });
  assert.deepEqual(workflowForCron("56 */6 * * *"), {
    kind: "backfill",
    workflow: "scan-dcinside-backfill.yml",
    destinations: ["dcinside"],
  });
  assert.deepEqual(workflowForCron("17 0,12 * * *"), {
    kind: "game-news",
    workflow: "scan-game-news.yml",
    destinations: ["game-news"],
  });
  assert.throws(
    () => workflowForCron("0 * * * *"),
    /Unsupported scheduler cron/,
  );
});

test("deployed Cron triggers exactly match the supported schedules", () => {
  assert.deepEqual(
    [...WRANGLER_CONFIG.triggers.crons].sort(),
    Object.keys(SCHEDULES).sort()
  );
  assert.equal(WRANGLER_CONFIG.vars.GAME_NEWS_DISPATCH_ENABLED, "1");
  assert.equal(
    WRANGLER_CONFIG.vars.GAME_NEWS_GITHUB_REPOSITORY,
    "TodayCommunity",
  );
  assert.ok(WRANGLER_CONFIG.triggers.crons.includes("17 0,12 * * *"));
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
    status: "completed",
    kind: "hot",
    destinations: [
      {
        destination: "dcinside",
        repository: "TodayCommunity-Public",
        status: "dispatched",
        kind: "hot",
        workflow: "scan-dcinside.yml",
        ref: "main",
      },
    ],
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
    status: "completed",
    kind: "hot",
    destinations: [
      {
        destination: "dcinside",
        repository: "TodayCommunity-Public",
        status: "skipped",
        kind: "hot",
        workflow: "scan-dcinside.yml",
        reason: "recent_same_workflow_dispatch",
        runId: 101,
      },
    ],
  });
});

test("FM dispatch is opt-in and disabled mode needs no private repository binding", async () => {
  const env = { ...ENV };
  delete env.FM_GITHUB_REPOSITORY;
  let nowCalls = 0;
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return options.method === "GET"
      ? jsonResponse({ workflow_runs: [] })
      : noContentResponse();
  };

  const result = await dispatchScheduledWorkflow({
    cron: "7,22,37,52 * * * *",
    env,
    fetchImpl,
    now: () => {
      nowCalls += 1;
      return NOW;
    },
  });

  assert.equal(nowCalls, 1);
  assert.equal(result.destinations.length, 1);
  assert.equal(result.destinations[0].destination, "dcinside");
  assert.equal(calls.length, 2);
  assert.ok(calls.every(({ url }) => url.includes("TodayCommunity-Public")));
});

test("enabled Hot dispatches public DC and private FM independently with one captured timestamp", async () => {
  const env = {
    ...ENV,
    FM_DISPATCH_ENABLED: "1",
    FM_GITHUB_REPOSITORY: "TodayCommunity",
  };
  let nowCalls = 0;
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    return options.method === "GET"
      ? jsonResponse({ workflow_runs: [] })
      : noContentResponse();
  };

  const result = await dispatchScheduledWorkflow({
    cron: "7,22,37,52 * * * *",
    env,
    fetchImpl,
    now: () => {
      nowCalls += 1;
      return NOW;
    },
  });

  assert.equal(nowCalls, 1);
  assert.deepEqual(
    result.destinations.map(({ destination, status }) => ({
      destination,
      status,
    })),
    [
      { destination: "dcinside", status: "dispatched" },
      { destination: "fmkorea", status: "dispatched" },
    ],
  );
  assert.equal(calls.length, 4);

  const publicPost = calls.find(
    ({ url, options }) =>
      options.method === "POST" && url.includes("TodayCommunity-Public"),
  );
  const fmPost = calls.find(
    ({ url, options }) =>
      options.method === "POST" &&
      url.includes("/TodayCommunity/") &&
      !url.includes("TodayCommunity-Public"),
  );
  assert.ok(publicPost);
  assert.ok(fmPost);
  assert.deepEqual(JSON.parse(publicPost.options.body), { ref: "main" });
  assert.deepEqual(JSON.parse(fmPost.options.body), {
    ref: "main",
    inputs: {
      dispatched_at: "2026-07-20T00:20:00.000Z",
      persist: "true",
      max_pages_per_target: "0",
    },
  });
  assert.match(fmPost.url, /scan-fmkorea\.yml\/dispatches$/);
  assert.equal(
    fmPost.options.headers.Authorization,
    publicPost.options.headers.Authorization,
  );
});

test("Backfill remains public-only even when FM dispatch is enabled", async () => {
  const calls = [];
  const result = await dispatchScheduledWorkflow({
    cron: "56 */6 * * *",
    env: { ...ENV, FM_DISPATCH_ENABLED: "1" },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return options.method === "GET"
        ? jsonResponse({ workflow_runs: [] })
        : noContentResponse();
    },
    now: () => NOW,
  });

  assert.equal(result.destinations.length, 1);
  assert.equal(result.destinations[0].workflow, "scan-dcinside-backfill.yml");
  assert.equal(calls.length, 2);
  assert.ok(calls.every(({ url }) => url.includes("TodayCommunity-Public")));
});

test("game-news cron is disabled when its flag is absent", async () => {
  const calls = [];
  const result = await dispatchScheduledWorkflow({
    cron: "17 0,12 * * *",
    env: {},
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      throw new Error("disabled game-news dispatch must not call GitHub");
    },
    now: () => NOW,
  });

  assert.deepEqual(result, {
    status: "completed",
    kind: "game-news",
    destinations: [],
  });
  assert.equal(calls.length, 0);
});

test("enabled game-news cron dispatches only its private workflow with persistent inputs", async () => {
  const calls = [];
  const result = await dispatchScheduledWorkflow({
    cron: "17 0,12 * * *",
    env: {
      ...ENV,
      GAME_NEWS_DISPATCH_ENABLED: "1",
      GAME_NEWS_GITHUB_REPOSITORY: "TodayCommunity",
    },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return options.method === "GET"
        ? jsonResponse({ workflow_runs: [] })
        : noContentResponse();
    },
    now: () => NOW,
  });

  assert.deepEqual(result, {
    status: "completed",
    kind: "game-news",
    destinations: [
      {
        destination: "game-news",
        repository: "TodayCommunity",
        status: "dispatched",
        kind: "game-news",
        workflow: "scan-game-news.yml",
        ref: "main",
      },
    ],
  });
  assert.equal(calls.length, 2);
  assert.ok(calls.every(({ url }) => !url.includes("TodayCommunity-Public")));
  assert.match(calls[1].url, /scan-game-news\.yml\/dispatches$/);
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    ref: "main",
    inputs: {
      dispatched_at: "2026-07-20T00:20:00.000Z",
      persist: "true",
    },
  });
});

test("FM and game-news managed runs remain independent in the same private repository", async () => {
  const gameCalls = [];
  const gameResult = await dispatchScheduledWorkflow({
    cron: "17 0,12 * * *",
    env: {
      ...ENV,
      GAME_NEWS_DISPATCH_ENABLED: "1",
      GAME_NEWS_GITHUB_REPOSITORY: "TodayCommunity",
    },
    fetchImpl: async (url, options) => {
      gameCalls.push({ url, options });
      return options.method === "GET"
        ? jsonResponse({
            workflow_runs: [
              {
                id: 601,
                event: "workflow_dispatch",
                status: "in_progress",
                head_branch: "main",
                path: ".github/workflows/scan-fmkorea.yml",
                created_at: "2026-07-20T00:15:00Z",
              },
            ],
          })
        : noContentResponse();
    },
    now: () => NOW,
  });
  assert.equal(gameResult.destinations[0].status, "dispatched");
  assert.equal(
    gameCalls.filter(({ options }) => options.method === "POST").length,
    1,
  );

  const hotCalls = [];
  const hotResult = await dispatchScheduledWorkflow({
    cron: "7,22,37,52 * * * *",
    env: {
      ...ENV,
      FM_DISPATCH_ENABLED: "1",
      FM_GITHUB_REPOSITORY: "TodayCommunity",
    },
    fetchImpl: async (url, options) => {
      hotCalls.push({ url, options });
      if (options.method === "POST") {
        return noContentResponse();
      }
      if (url.includes("TodayCommunity-Public")) {
        return jsonResponse({ workflow_runs: [] });
      }
      return jsonResponse({
        workflow_runs: [
          {
            id: 602,
            event: "workflow_dispatch",
            status: "queued",
            head_branch: "main",
            path: ".github/workflows/scan-game-news.yml",
            created_at: "2026-07-20T00:15:00Z",
          },
        ],
      });
    },
    now: () => NOW,
  });
  assert.equal(hotResult.destinations[0].status, "dispatched");
  assert.equal(hotResult.destinations[1].status, "dispatched");
});

test("an active game-news run suppresses only a new game-news dispatch", async () => {
  const calls = [];
  const result = await dispatchScheduledWorkflow({
    cron: "17 0,12 * * *",
    env: {
      ...ENV,
      GAME_NEWS_DISPATCH_ENABLED: "1",
      GAME_NEWS_GITHUB_REPOSITORY: "TodayCommunity",
    },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return jsonResponse({
        workflow_runs: [
          {
            id: 603,
            event: "workflow_dispatch",
            status: "queued",
            head_branch: "main",
            path: ".github/workflows/scan-game-news.yml",
            created_at: "2026-07-19T00:00:00Z",
          },
        ],
      });
    },
    now: () => NOW,
  });

  assert.equal(calls.length, 1);
  assert.deepEqual(result.destinations[0], {
    destination: "game-news",
    repository: "TodayCommunity",
    status: "skipped",
    kind: "game-news",
    workflow: "scan-game-news.yml",
    reason: "managed_workflow_active",
    runId: 603,
  });
});

test("an offline queued FM run does not suppress an eligible public DC dispatch", async () => {
  const calls = [];
  const result = await dispatchScheduledWorkflow({
    cron: "7,22,37,52 * * * *",
    env: {
      ...ENV,
      FM_DISPATCH_ENABLED: "1",
      FM_GITHUB_REPOSITORY: "TodayCommunity",
    },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (options.method === "POST") {
        return noContentResponse();
      }
      if (url.includes("TodayCommunity-Public")) {
        return jsonResponse({ workflow_runs: [] });
      }
      return jsonResponse({
        workflow_runs: [
          {
            id: 404,
            event: "workflow_dispatch",
            status: "queued",
            head_branch: "main",
            path: ".github/workflows/scan-fmkorea.yml",
            created_at: "2026-07-19T00:00:00Z",
          },
        ],
      });
    },
    now: () => NOW,
  });

  assert.deepEqual(
    result.destinations.map(({ destination, status, reason }) => ({
      destination,
      status,
      reason,
    })),
    [
      { destination: "dcinside", status: "dispatched", reason: undefined },
      {
        destination: "fmkorea",
        status: "skipped",
        reason: "managed_workflow_active",
      },
    ],
  );
  assert.equal(
    calls.filter(({ options }) => options.method === "POST").length,
    1,
  );
  assert.ok(
    calls.some(
      ({ url, options }) =>
        options.method === "POST" && url.includes("TodayCommunity-Public"),
    ),
  );
});

test("a recent completed FM dispatch suppresses only the FM lane", async () => {
  const calls = [];
  const result = await dispatchScheduledWorkflow({
    cron: "7,22,37,52 * * * *",
    env: {
      ...ENV,
      FM_DISPATCH_ENABLED: "1",
      FM_GITHUB_REPOSITORY: "TodayCommunity",
    },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (options.method === "POST") {
        return noContentResponse();
      }
      if (url.includes("TodayCommunity-Public")) {
        return jsonResponse({ workflow_runs: [] });
      }
      return jsonResponse({
        workflow_runs: [
          {
            id: 454,
            event: "workflow_dispatch",
            status: "completed",
            head_branch: "main",
            path: ".github/workflows/scan-fmkorea.yml",
            created_at: "2026-07-20T00:15:00Z",
          },
        ],
      });
    },
    now: () => NOW,
  });

  assert.equal(result.destinations[0].status, "dispatched");
  assert.equal(result.destinations[1].status, "skipped");
  assert.equal(
    result.destinations[1].reason,
    "recent_same_workflow_dispatch",
  );
  assert.ok(
    calls.some(
      ({ url, options }) =>
        options.method === "POST" && url.includes("TodayCommunity-Public"),
    ),
  );
});

test("a public active run does not suppress an eligible private FM dispatch", async () => {
  const calls = [];
  const result = await dispatchScheduledWorkflow({
    cron: "7,22,37,52 * * * *",
    env: {
      ...ENV,
      FM_DISPATCH_ENABLED: "1",
      FM_GITHUB_REPOSITORY: "TodayCommunity",
    },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      if (options.method === "POST") {
        return noContentResponse();
      }
      if (!url.includes("TodayCommunity-Public")) {
        return jsonResponse({ workflow_runs: [] });
      }
      return jsonResponse({
        workflow_runs: [
          {
            id: 505,
            event: "schedule",
            status: "in_progress",
            path: ".github/workflows/scan-dcinside-backfill.yml",
          },
        ],
      });
    },
    now: () => NOW,
  });

  assert.equal(result.destinations[0].status, "skipped");
  assert.equal(result.destinations[1].status, "dispatched");
  assert.ok(
    calls.some(
      ({ url, options }) =>
        options.method === "POST" &&
        url.includes("/TodayCommunity/") &&
        !url.includes("TodayCommunity-Public"),
    ),
  );
});

test("Hot skips while another managed workflow is active", () => {
  const schedule = SCHEDULES["7,22,37,52 * * * *"];
  const decision = decideDispatch({
    runs: [
      {
        id: 202,
        event: "schedule",
        status: "in_progress",
        path: ".github/workflows/scan-dcinside-backfill.yml",
      },
    ],
    schedule: { kind: schedule.kind, workflow: schedule.workflow },
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
  const configuredSchedule = SCHEDULES["56 */6 * * *"];
  const schedule = {
    kind: configuredSchedule.kind,
    workflow: configuredSchedule.workflow,
  };
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
    (error) => {
      assert.ok(error instanceof ScheduledDispatchError);
      assert.equal(error.kind, "backfill");
      assert.equal(error.destinations.length, 1);
      assert.equal(error.destinations[0].destination, "dcinside");
      assert.equal(error.destinations[0].status, "failed");
      assert.match(error.destinations[0].error, /HTTP 403.*Forbidden/);
      return true;
    },
  );
});

test("a failing FM destination is reported only after the public dispatch is attempted", async () => {
  const calls = [];
  await assert.rejects(
    dispatchScheduledWorkflow({
      cron: "7,22,37,52 * * * *",
      env: {
        ...ENV,
        FM_DISPATCH_ENABLED: "1",
        FM_GITHUB_REPOSITORY: "TodayCommunity",
      },
      fetchImpl: async (url, options) => {
        calls.push({ url, options });
        if (
          options.method === "GET" &&
          url.includes("/TodayCommunity/") &&
          !url.includes("TodayCommunity-Public")
        ) {
          return jsonResponse({ message: "private repository unavailable" }, 404);
        }
        return options.method === "GET"
          ? jsonResponse({ workflow_runs: [] })
          : noContentResponse();
      },
      now: () => NOW,
    }),
    (error) => {
      assert.ok(error instanceof ScheduledDispatchError);
      assert.deepEqual(
        error.destinations.map(({ destination, status }) => ({
          destination,
          status,
        })),
        [
          { destination: "dcinside", status: "dispatched" },
          { destination: "fmkorea", status: "failed" },
        ],
      );
      return true;
    },
  );

  assert.ok(
    calls.some(
      ({ url, options }) =>
        options.method === "POST" && url.includes("TodayCommunity-Public"),
    ),
  );
});

test("invalid FM enable flag is isolated after the public DC dispatch is attempted", async () => {
  const calls = [];
  await assert.rejects(
    dispatchScheduledWorkflow({
      cron: "7,22,37,52 * * * *",
      env: { ...ENV, FM_DISPATCH_ENABLED: "yes" },
      fetchImpl: async (url, options) => {
        calls.push({ url, options });
        return options.method === "GET"
          ? jsonResponse({ workflow_runs: [] })
          : noContentResponse();
      },
      now: () => NOW,
    }),
    (error) => {
      assert.ok(error instanceof ScheduledDispatchError);
      assert.deepEqual(
        error.destinations.map(({ destination, status }) => ({
          destination,
          status,
        })),
        [
          { destination: "dcinside", status: "dispatched" },
          { destination: "fmkorea", status: "failed" },
        ],
      );
      assert.match(
        error.destinations[1].error,
        /FM_DISPATCH_ENABLED must be either 0 or 1/,
      );
      return true;
    },
  );

  assert.equal(calls.length, 2);
  assert.ok(calls.every(({ url }) => url.includes("TodayCommunity-Public")));
});

test("Backfill ignores an invalid FM enable flag", async () => {
  const calls = [];
  const result = await dispatchScheduledWorkflow({
    cron: "56 */6 * * *",
    env: { ...ENV, FM_DISPATCH_ENABLED: "invalid" },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return options.method === "GET"
        ? jsonResponse({ workflow_runs: [] })
        : noContentResponse();
    },
    now: () => NOW,
  });

  assert.equal(result.status, "completed");
  assert.equal(result.destinations.length, 1);
  assert.equal(result.destinations[0].destination, "dcinside");
  assert.equal(calls.length, 2);
});

test("game-news cron ignores FM configuration and fails closed on an invalid game flag", async () => {
  const calls = [];
  await assert.rejects(
    dispatchScheduledWorkflow({
      cron: "17 0,12 * * *",
      env: {
        FM_DISPATCH_ENABLED: "invalid",
        GAME_NEWS_DISPATCH_ENABLED: "yes",
      },
      fetchImpl: async (url, options) => {
        calls.push({ url, options });
        return noContentResponse();
      },
      now: () => NOW,
    }),
    (error) => {
      assert.ok(error instanceof ScheduledDispatchError);
      assert.equal(error.kind, "game-news");
      assert.equal(error.destinations.length, 1);
      assert.equal(error.destinations[0].destination, "game-news");
      assert.equal(error.destinations[0].status, "failed");
      assert.match(
        error.destinations[0].error,
        /GAME_NEWS_DISPATCH_ENABLED must be either 0 or 1/,
      );
      return true;
    },
  );
  assert.equal(calls.length, 0);
});

test("non-game schedules ignore an invalid game-news enable flag", async () => {
  const calls = [];
  const result = await dispatchScheduledWorkflow({
    cron: "56 */6 * * *",
    env: { ...ENV, GAME_NEWS_DISPATCH_ENABLED: "invalid" },
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return options.method === "GET"
        ? jsonResponse({ workflow_runs: [] })
        : noContentResponse();
    },
    now: () => NOW,
  });

  assert.equal(result.status, "completed");
  assert.equal(result.destinations.length, 1);
  assert.equal(result.destinations[0].destination, "dcinside");
  assert.equal(calls.length, 2);
});
