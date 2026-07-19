const GITHUB_API_ROOT = "https://api.github.com";

export const GITHUB_API_VERSION = "2022-11-28";
export const RECENT_DISPATCH_WINDOW_MS = 10 * 60 * 1000;

export const SCHEDULES = Object.freeze({
  "7,22,37,52 * * * *": Object.freeze({
    kind: "hot",
    workflow: "scan-dcinside.yml",
  }),
  "56 */6 * * *": Object.freeze({
    kind: "backfill",
    workflow: "scan-dcinside-backfill.yml",
  }),
});

const MANAGED_WORKFLOWS = new Set(
  Object.values(SCHEDULES).map(({ workflow }) => workflow),
);

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
      isActiveRun(run) && MANAGED_WORKFLOWS.has(workflowFileForRun(run)),
  );
  if (schedule.kind === "hot" && activeManagedRuns.length > 0) {
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

export async function dispatchScheduledWorkflow({
  cron,
  env,
  fetchImpl = fetch,
  now = () => Date.now(),
  recentWindowMs = RECENT_DISPATCH_WINDOW_MS,
}) {
  const schedule = workflowForCron(cron);
  const token = requireEnv(env, "GITHUB_DISPATCH_TOKEN");
  const owner = requireEnv(env, "GITHUB_OWNER");
  const repository = requireEnv(env, "GITHUB_REPOSITORY");
  const ref = requireEnv(env, "GITHUB_REF");
  const repositoryPath = `${encodeURIComponent(owner)}/${encodeURIComponent(repository)}`;

  const runsUrl = `${GITHUB_API_ROOT}/repos/${repositoryPath}/actions/runs?per_page=100`;
  const runsResponse = await githubRequest(fetchImpl, runsUrl, {
    method: "GET",
    headers: githubHeaders(token),
  });
  if (!Array.isArray(runsResponse?.workflow_runs)) {
    throw new Error("GitHub workflow runs response did not contain workflow_runs");
  }

  const decision = decideDispatch({
    runs: runsResponse.workflow_runs,
    schedule,
    ref,
    nowMs: now(),
    recentWindowMs,
  });
  if (decision.action === "skip") {
    return {
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
  await githubRequest(fetchImpl, dispatchUrl, {
    method: "POST",
    headers: githubHeaders(token, true),
    body: JSON.stringify({ ref }),
  });

  return {
    status: "dispatched",
    kind: schedule.kind,
    workflow: schedule.workflow,
    ref,
  };
}

export default {
  async scheduled(controller, env) {
    const result = await dispatchScheduledWorkflow({
      cron: controller.cron,
      env,
    });
    console.log("TodayCommunity scheduler result", JSON.stringify(result));
    return result;
  },
};
