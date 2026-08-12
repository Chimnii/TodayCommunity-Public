const GITHUB_API_ROOT = "https://api.github.com";
const PUBLIC_DESTINATION = "dcinside";
const FMKOREA_DESTINATION = "fmkorea";
const GAME_NEWS_DESTINATION = "game-news";
const FMKOREA_WORKFLOW = "scan-fmkorea.yml";
const GAME_NEWS_WORKFLOW = "scan-game-news.yml";

export const GITHUB_API_VERSION = "2022-11-28";
export const RECENT_DISPATCH_WINDOW_MS = 10 * 60 * 1000;

export const SCHEDULES = Object.freeze({
  "7,22,37,52 * * * *": Object.freeze({
    kind: "hot",
    workflow: "scan-dcinside.yml",
    destinations: Object.freeze([PUBLIC_DESTINATION, FMKOREA_DESTINATION]),
  }),
  "56 */6 * * *": Object.freeze({
    kind: "backfill",
    workflow: "scan-dcinside-backfill.yml",
    destinations: Object.freeze([PUBLIC_DESTINATION]),
  }),
  "17 0,12 * * *": Object.freeze({
    kind: "game-news",
    workflow: GAME_NEWS_WORKFLOW,
    destinations: Object.freeze([GAME_NEWS_DESTINATION]),
  }),
});

const PUBLIC_MANAGED_WORKFLOWS = new Set([
  "scan-dcinside.yml",
  "scan-dcinside-backfill.yml",
]);

const FMKOREA_MANAGED_WORKFLOWS = new Set([FMKOREA_WORKFLOW]);
const GAME_NEWS_MANAGED_WORKFLOWS = new Set([GAME_NEWS_WORKFLOW]);

export function workflowForCron(cron) {
  const schedule = SCHEDULES[cron];
  if (!schedule) {
    throw new Error(`Unsupported scheduler cron: ${cron || "<missing>"}`);
  }
  return schedule;
}

function requireEnv(env, name) {
  const value = env?.[name];
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`Missing required Worker binding: ${name}`);
  }
  return value.trim();
}

function workflowFileForRun(run) {
  if (typeof run?.path !== "string") {
    return "";
  }
  const pathWithoutRef = run.path.split("@")[0];
  return pathWithoutRef.split("/").at(-1) || "";
}

function isActiveRun(run) {
  return typeof run?.status === "string" && run.status !== "completed";
}

function isRecentSameDispatch(run, workflow, ref, nowMs, windowMs) {
  if (
    run?.event !== "workflow_dispatch" ||
    workflowFileForRun(run) !== workflow ||
    run?.head_branch !== ref
  ) {
    return false;
  }

  const createdAtMs = Date.parse(run.created_at);
  if (!Number.isFinite(createdAtMs)) {
    return false;
  }
  const ageMs = nowMs - createdAtMs;
  return ageMs >= 0 && ageMs <= windowMs;
}

export function decideDispatch({
  runs,
  schedule,
  ref,
  nowMs,
  recentWindowMs = RECENT_DISPATCH_WINDOW_MS,
  managedWorkflows = PUBLIC_MANAGED_WORKFLOWS,
}) {
  const recentSameDispatch = runs.find((run) =>
    isRecentSameDispatch(
      run,
      schedule.workflow,
      ref,
      nowMs,
      recentWindowMs,
    ),
  );
  if (recentSameDispatch) {
    return {
      action: "skip",
      reason: "recent_same_workflow_dispatch",
      runId: recentSameDispatch.id,
    };
  }

  const activeManagedRuns = runs.filter(
    (run) =>
      isActiveRun(run) && managedWorkflows.has(workflowFileForRun(run)),
  );
  if (schedule.kind !== "backfill" && activeManagedRuns.length > 0) {
    return {
      action: "skip",
      reason: "managed_workflow_active",
      runId: activeManagedRuns[0].id,
    };
  }

  if (schedule.kind === "backfill") {
    const activeBackfill = activeManagedRuns.find(
      (run) => workflowFileForRun(run) === schedule.workflow,
    );
    if (activeBackfill) {
      return {
        action: "skip",
        reason: "backfill_active",
        runId: activeBackfill.id,
      };
    }
  }

  return { action: "dispatch" };
}

function optionalDispatchConfig(env, bindingName) {
  const value = env?.[bindingName];
  if (value === undefined || value === null || String(value).trim() === "") {
    return { enabled: false };
  }

  const normalized = String(value).trim();
  if (normalized === "0") {
    return { enabled: false };
  }
  if (normalized === "1") {
    return { enabled: true };
  }
  return {
    enabled: true,
    error: new Error(`${bindingName} must be either 0 or 1`),
  };
}

function destinationsForSchedule(schedule, env, nowMs) {
  const destinations = [];
  for (const destination of schedule.destinations) {
    if (destination === PUBLIC_DESTINATION) {
      destinations.push({
        destination,
        repositoryBinding: "GITHUB_REPOSITORY",
        schedule: { kind: schedule.kind, workflow: schedule.workflow },
        managedWorkflows: PUBLIC_MANAGED_WORKFLOWS,
      });
      continue;
    }

    if (destination === FMKOREA_DESTINATION) {
      const config = optionalDispatchConfig(env, "FM_DISPATCH_ENABLED");
      if (config.enabled) {
        destinations.push({
          destination,
          repositoryBinding: "FM_GITHUB_REPOSITORY",
          schedule: { kind: "hot", workflow: FMKOREA_WORKFLOW },
          managedWorkflows: FMKOREA_MANAGED_WORKFLOWS,
          inputs: {
            dispatched_at: new Date(nowMs).toISOString(),
            persist: "true",
            max_pages_per_target: "0",
          },
          configurationError: config.error,
        });
      }
      continue;
    }

    if (destination === GAME_NEWS_DESTINATION) {
      const config = optionalDispatchConfig(
        env,
        "GAME_NEWS_DISPATCH_ENABLED",
      );
      if (config.enabled) {
        destinations.push({
          destination,
          repositoryBinding: "GAME_NEWS_GITHUB_REPOSITORY",
          schedule: { kind: "game-news", workflow: GAME_NEWS_WORKFLOW },
          managedWorkflows: GAME_NEWS_MANAGED_WORKFLOWS,
          inputs: {
            dispatched_at: new Date(nowMs).toISOString(),
            persist: "true",
          },
          configurationError: config.error,
        });
      }
      continue;
    }

    throw new Error(`Unsupported scheduler destination: ${destination}`);
  }
  return destinations;
}

function githubHeaders(token, includeJsonBody = false) {
  const headers = {
    Accept: "application/vnd.github+json",
    Authorization: `Bearer ${token}`,
    "User-Agent": "TodayCommunity-Cloudflare-Scheduler",
    "X-GitHub-Api-Version": GITHUB_API_VERSION,
  };
  if (includeJsonBody) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

async function githubRequest(fetchImpl, url, options) {
  const response = await fetchImpl(url, options);
  if (!response.ok) {
    const responseBody = (await response.text()).replace(/\s+/g, " ").trim();
    const detail = responseBody ? `: ${responseBody.slice(0, 500)}` : "";
    throw new Error(
      `GitHub API ${options.method} ${url} failed with HTTP ${response.status}${detail}`,
    );
  }

  if (response.status === 204) {
    return null;
  }
  return response.json();
}

async function dispatchDestination({
  destination,
  repositoryBinding,
  schedule,
  managedWorkflows,
  env,
  fetchImpl,
  nowMs,
  recentWindowMs,
  inputs,
  configurationError,
}) {
  if (configurationError) {
    throw configurationError;
  }

  const token = requireEnv(env, "GITHUB_DISPATCH_TOKEN");
  const owner = requireEnv(env, "GITHUB_OWNER");
  const repository = requireEnv(env, repositoryBinding);
  const ref = requireEnv(env, "GITHUB_REF");
  const repositoryPath = `${encodeURIComponent(owner)}/${encodeURIComponent(repository)}`;

  const runsUrl = `${GITHUB_API_ROOT}/repos/${repositoryPath}/actions/runs?per_page=100`;
  const runsResponse = await githubRequest(fetchImpl, runsUrl, {
    method: "GET",
    headers: githubHeaders(token),
  });
  if (!Array.isArray(runsResponse?.workflow_runs)) {
    throw new Error(
      `${destination} GitHub workflow runs response did not contain workflow_runs`,
    );
  }

  const decision = decideDispatch({
    runs: runsResponse.workflow_runs,
    schedule,
    ref,
    nowMs,
    recentWindowMs,
    managedWorkflows,
  });
  if (decision.action === "skip") {
    return {
      destination,
      repository,
      status: "skipped",
      kind: schedule.kind,
      workflow: schedule.workflow,
      reason: decision.reason,
      runId: decision.runId,
    };
  }

  const dispatchUrl =
    `${GITHUB_API_ROOT}/repos/${repositoryPath}/actions/workflows/` +
    `${encodeURIComponent(schedule.workflow)}/dispatches`;
  const body = { ref };
  if (inputs !== undefined) {
    body.inputs = inputs;
  }
  await githubRequest(fetchImpl, dispatchUrl, {
    method: "POST",
    headers: githubHeaders(token, true),
    body: JSON.stringify(body),
  });

  return {
    destination,
    repository,
    status: "dispatched",
    kind: schedule.kind,
    workflow: schedule.workflow,
    ref,
  };
}

export class ScheduledDispatchError extends AggregateError {
  constructor(kind, failures, destinations) {
    super(
      failures,
      `Scheduled ${kind} dispatch failed for ${destinations
        .filter(({ status }) => status === "failed")
        .map(({ destination }) => destination)
        .join(", ")}`,
    );
    this.name = "ScheduledDispatchError";
    this.kind = kind;
    this.destinations = destinations;
  }
}

export async function dispatchScheduledWorkflow({
  cron,
  env,
  fetchImpl = fetch,
  now = () => Date.now(),
  recentWindowMs = RECENT_DISPATCH_WINDOW_MS,
}) {
  const schedule = workflowForCron(cron);
  const nowMs = now();
  if (!Number.isFinite(nowMs)) {
    throw new Error("Scheduler clock returned an invalid timestamp");
  }

  const destinations = destinationsForSchedule(schedule, env, nowMs);

  const settled = await Promise.allSettled(
    destinations.map((destination) =>
      dispatchDestination({
        ...destination,
        env,
        fetchImpl,
        nowMs,
        recentWindowMs,
      }),
    ),
  );
  const results = settled.map((outcome, index) => {
    if (outcome.status === "fulfilled") {
      return outcome.value;
    }
    const error =
      outcome.reason instanceof Error
        ? outcome.reason.message
        : String(outcome.reason);
    return {
      destination: destinations[index].destination,
      status: "failed",
      kind: destinations[index].schedule.kind,
      workflow: destinations[index].schedule.workflow,
      error,
    };
  });
  const failures = settled
    .filter(({ status }) => status === "rejected")
    .map(({ reason }) => reason);
  if (failures.length > 0) {
    throw new ScheduledDispatchError(schedule.kind, failures, results);
  }

  return {
    status: "completed",
    kind: schedule.kind,
    destinations: results,
  };
}

export default {
  async scheduled(controller, env) {
    try {
      const result = await dispatchScheduledWorkflow({
        cron: controller.cron,
        env,
      });
      console.log("TodayCommunity scheduler result", JSON.stringify(result));
      return result;
    } catch (error) {
      console.error(
        "TodayCommunity scheduler failure",
        JSON.stringify({
          name: error?.name,
          message: error?.message,
          kind: error?.kind,
          destinations: error?.destinations,
        }),
      );
      throw error;
    }
  },
};
