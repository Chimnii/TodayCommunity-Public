import {
  AUTH_STATE_ADMIN,
  AUTH_STATE_AUTHENTICATED,
  AuthConfigurationError,
  createSessionToken,
  expiredSessionCookie,
  generateSecretLinkToken,
  hashSecretLinkToken,
  loginClientKeyHash,
  resolveAuthIdentity,
  sessionCookie,
  verifyAdminPassword,
} from "../_auth.js";

const MAX_BODY_BYTES = 4096;
const LOGIN_WINDOW_MS = 15 * 60 * 1000;
const LOGIN_LOCK_MS = 15 * 60 * 1000;
const MAX_LOGIN_FAILURES = 5;
const ALLOWED_EXPIRY_DAYS = new Set([0, 30, 90, 365]);

class RequestError extends Error {
  constructor(status, message, cookies = []) {
    super(message);
    this.status = status;
    this.cookies = cookies;
  }
}

function jsonResponse(body, status = 200, cookies = []) {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    Vary: "Cookie",
  });
  for (const cookie of cookies) {
    headers.append("Set-Cookie", cookie);
  }
  return new Response(JSON.stringify(body), { status, headers });
}

function resourceFromRequest(request) {
  const pathname = new URL(request.url).pathname.replace(/\/+$/u, "");
  const prefix = "/api/auth/";
  if (!pathname.startsWith(prefix)) {
    return "";
  }
  return pathname.slice(prefix.length);
}

function requestIsSameOriginWrite(request) {
  const url = new URL(request.url);
  const origin = request.headers.get("Origin");
  const fetchSite = request.headers.get("Sec-Fetch-Site");
  return (
    (!origin || origin === url.origin)
    && (!fetchSite || fetchSite === "same-origin" || fetchSite === "none")
    && request.headers.get("X-TodayCommunity-Auth") === "1"
  );
}

async function readJsonBody(request) {
  if (!String(request.headers.get("Content-Type") || "")
    .toLowerCase()
    .startsWith("application/json")) {
    throw new RequestError(415, "JSON 요청만 지원합니다.");
  }
  const contentLength = Number(request.headers.get("Content-Length") || 0);
  if (Number.isFinite(contentLength) && contentLength > MAX_BODY_BYTES) {
    throw new RequestError(413, "요청이 너무 큽니다.");
  }
  const raw = await request.text();
  if (new TextEncoder().encode(raw).length > MAX_BODY_BYTES) {
    throw new RequestError(413, "요청이 너무 큽니다.");
  }
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    throw new RequestError(400, "JSON 형식이 올바르지 않습니다.");
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new RequestError(400, "요청 본문은 객체여야 합니다.");
  }
  return body;
}

function resultRows(result) {
  return Array.isArray(result?.results) ? result.results : [];
}

async function queryAll(db, sql, bindings = []) {
  return resultRows(await db.prepare(sql).bind(...bindings).all());
}

function requireDatabase(env) {
  if (!env?.DB?.prepare) {
    throw new AuthConfigurationError("D1 binding is unavailable");
  }
  return env.DB;
}

async function requireAdmin(request, env) {
  const identity = await resolveAuthIdentity(request, env);
  if (identity.state !== AUTH_STATE_ADMIN) {
    throw new RequestError(
      401,
      "관리자 로그인이 필요합니다.",
      identity.cookies_to_clear
    );
  }
  return identity;
}

function publicIdentity(identity) {
  return {
    state: identity.state,
    authenticated: identity.authenticated,
    capabilities: identity.capabilities,
  };
}

async function getSession(request, env) {
  const identity = await resolveAuthIdentity(request, env);
  return jsonResponse(
    publicIdentity(identity),
    200,
    identity.cookies_to_clear
  );
}

async function getLoginLimit(db, clientKey) {
  const rows = await queryAll(
    db,
    `
    SELECT failure_count, window_started_at, locked_until
    FROM auth_login_limits
    WHERE client_key_hash = ?
    LIMIT 1
    `,
    [clientKey]
  );
  return rows[0] ?? null;
}

function validDateMs(value) {
  const result = Date.parse(String(value || ""));
  return Number.isFinite(result) ? result : null;
}

async function assertLoginAllowed(db, clientKey, nowMs) {
  const row = await getLoginLimit(db, clientKey);
  const lockedUntil = validDateMs(row?.locked_until);
  if (lockedUntil !== null && lockedUntil > nowMs) {
    throw new RequestError(429, "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.");
  }
}

async function recordLoginFailure(db, clientKey, nowMs) {
  const row = await getLoginLimit(db, clientKey);
  const previousWindow = validDateMs(row?.window_started_at);
  const sameWindow = previousWindow !== null && nowMs - previousWindow < LOGIN_WINDOW_MS;
  const failureCount = sameWindow
    ? Math.min(1000, Number(row?.failure_count || 0) + 1)
    : 1;
  const windowStartedAt = new Date(sameWindow ? previousWindow : nowMs).toISOString();
  const lockedUntil = failureCount >= MAX_LOGIN_FAILURES
    ? new Date(nowMs + LOGIN_LOCK_MS).toISOString()
    : null;
  const updatedAt = new Date(nowMs).toISOString();
  await queryAll(
    db,
    `
    INSERT INTO auth_login_limits (
      client_key_hash, failure_count, window_started_at, locked_until, updated_at
    ) VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(client_key_hash) DO UPDATE SET
      failure_count = excluded.failure_count,
      window_started_at = excluded.window_started_at,
      locked_until = excluded.locked_until,
      updated_at = excluded.updated_at
    RETURNING client_key_hash
    `,
    [clientKey, failureCount, windowStartedAt, lockedUntil, updatedAt]
  );
  return failureCount;
}

async function clearLoginFailures(db, clientKey) {
  await queryAll(
    db,
    "DELETE FROM auth_login_limits WHERE client_key_hash = ? RETURNING client_key_hash",
    [clientKey]
  );
}

async function loginAdmin(request, env) {
  const db = requireDatabase(env);
  const clientKey = await loginClientKeyHash(request, env);
  const nowMs = Date.now();
  await assertLoginAllowed(db, clientKey, nowMs);
  const body = await readJsonBody(request);
  const password = typeof body.password === "string" ? body.password : "";
  if (!await verifyAdminPassword(password, env.TC_AUTH_ADMIN_VERIFIER)) {
    const failures = await recordLoginFailure(db, clientKey, nowMs);
    if (failures >= MAX_LOGIN_FAILURES) {
      throw new RequestError(429, "로그인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.");
    }
    throw new RequestError(401, "비밀번호가 올바르지 않습니다.");
  }
  await clearLoginFailures(db, clientKey);
  const token = await createSessionToken(env, AUTH_STATE_ADMIN, null, nowMs);
  return jsonResponse(
    {
      state: AUTH_STATE_ADMIN,
      authenticated: true,
      capabilities: {
        rate: true,
        hide: true,
        manage_rules: true,
        manage_auth: true,
      },
    },
    200,
    [sessionCookie(AUTH_STATE_ADMIN, token)]
  );
}

function normalizeLinkLabel(value) {
  const label = typeof value === "string" ? value.trim() : "";
  return label && Array.from(label).length <= 80 ? label : null;
}

function normalizeExpiryDays(value) {
  const days = Number(value ?? 0);
  return Number.isSafeInteger(days) && ALLOWED_EXPIRY_DAYS.has(days) ? days : null;
}

function publicLink(row, nowMs = Date.now()) {
  const expiresAt = row.expires_at || null;
  const revokedAt = row.revoked_at || null;
  const expired = expiresAt !== null && Date.parse(expiresAt) <= nowMs;
  return {
    id: Number(row.id),
    label: String(row.label),
    created_at: String(row.created_at),
    last_used_at: row.last_used_at || null,
    expires_at: expiresAt,
    revoked_at: revokedAt,
    active: revokedAt === null && !expired,
  };
}

async function listLinks(db) {
  const rows = await queryAll(
    db,
    `
    SELECT id, label, created_at, last_used_at, expires_at, revoked_at
    FROM auth_secret_links
    ORDER BY id DESC
    LIMIT 200
    `
  );
  return rows.map((row) => publicLink(row));
}

async function getLinks(request, env) {
  await requireAdmin(request, env);
  return jsonResponse({ items: await listLinks(requireDatabase(env)) });
}

async function createLink(request, env) {
  await requireAdmin(request, env);
  const db = requireDatabase(env);
  const body = await readJsonBody(request);
  const label = normalizeLinkLabel(body.label);
  const expiresInDays = normalizeExpiryDays(body.expires_in_days);
  if (!label || expiresInDays === null) {
    throw new RequestError(400, "링크 이름 또는 만료 설정이 올바르지 않습니다.");
  }
  const nowMs = Date.now();
  const createdAt = new Date(nowMs).toISOString();
  const expiresAt = expiresInDays === 0
    ? null
    : new Date(nowMs + expiresInDays * 24 * 60 * 60 * 1000).toISOString();
  const rawToken = generateSecretLinkToken();
  const tokenHash = await hashSecretLinkToken(rawToken);
  const inserted = await queryAll(
    db,
    `
    INSERT INTO auth_secret_links (
      label, token_hash, created_at, last_used_at, expires_at, revoked_at
    ) VALUES (?, ?, ?, NULL, ?, NULL)
    RETURNING id, label, created_at, last_used_at, expires_at, revoked_at
    `,
    [label, tokenHash, createdAt, expiresAt]
  );
  if (inserted.length !== 1) {
    throw new Error("Secret link insertion failed");
  }
  const secretUrl = new URL("/owner/", request.url);
  secretUrl.hash = `token=${rawToken}`;
  return jsonResponse(
    {
      item: publicLink(inserted[0], nowMs),
      secret_url: secretUrl.toString(),
      warning: "이 주소는 다시 표시되지 않습니다.",
    },
    201
  );
}

function normalizePositiveId(value) {
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

async function revokeLink(request, env) {
  await requireAdmin(request, env);
  const db = requireDatabase(env);
  const body = await readJsonBody(request);
  const id = normalizePositiveId(body.id);
  if (!id) {
    throw new RequestError(400, "폐기할 링크 식별자가 올바르지 않습니다.");
  }
  const revokedAt = new Date().toISOString();
  const updated = await queryAll(
    db,
    `
    UPDATE auth_secret_links
    SET revoked_at = coalesce(revoked_at, ?)
    WHERE id = ?
    RETURNING id
    `,
    [revokedAt, id]
  );
  if (updated.length !== 1) {
    throw new RequestError(404, "시크릿 링크를 찾지 못했습니다.");
  }
  return jsonResponse({ items: await listLinks(db) });
}

async function exchangeSecretLink(request, env) {
  const db = requireDatabase(env);
  const body = await readJsonBody(request);
  const tokenHash = await hashSecretLinkToken(body.token);
  if (!tokenHash) {
    throw new RequestError(401, "유효하지 않거나 만료된 시크릿 링크입니다.");
  }
  const nowMs = Date.now();
  const nowIso = new Date(nowMs).toISOString();
  const updated = await queryAll(
    db,
    `
    UPDATE auth_secret_links
    SET last_used_at = ?
    WHERE token_hash = ?
      AND revoked_at IS NULL
      AND (expires_at IS NULL OR expires_at > ?)
    RETURNING id
    `,
    [nowIso, tokenHash, nowIso]
  );
  if (updated.length !== 1) {
    throw new RequestError(401, "유효하지 않거나 만료된 시크릿 링크입니다.");
  }
  const credentialId = Number(updated[0].id);
  const sessionToken = await createSessionToken(
    env,
    AUTH_STATE_AUTHENTICATED,
    credentialId,
    nowMs
  );
  return jsonResponse(
    {
      state: AUTH_STATE_AUTHENTICATED,
      authenticated: true,
      capabilities: {
        rate: true,
        hide: true,
        manage_rules: true,
        manage_auth: false,
      },
    },
    200,
    [sessionCookie(AUTH_STATE_AUTHENTICATED, sessionToken)]
  );
}

export async function onRequestGet(context) {
  try {
    const resource = resourceFromRequest(context.request);
    if (resource === "session") {
      return await getSession(context.request, context.env);
    }
    if (resource === "admin/links") {
      return await getLinks(context.request, context.env);
    }
    return jsonResponse({ error: "지원하지 않는 경로입니다." }, 404);
  } catch (error) {
    if (error instanceof RequestError) {
      return jsonResponse({ error: error.message }, error.status, error.cookies);
    }
    if (error instanceof AuthConfigurationError) {
      console.error("Authentication configuration failed", error.message);
      return jsonResponse({ error: "인증 서비스를 사용할 수 없습니다." }, 503);
    }
    console.error("Authentication API failed", error);
    return jsonResponse({ error: "요청을 처리하지 못했습니다." }, 500);
  }
}

export async function onRequestPost(context) {
  try {
    if (!requestIsSameOriginWrite(context.request)) {
      throw new RequestError(403, "허용되지 않은 인증 요청입니다.");
    }
    const resource = resourceFromRequest(context.request);
    if (resource === "admin/login") {
      return await loginAdmin(context.request, context.env);
    }
    if (resource === "admin/logout") {
      return jsonResponse(
        { state: "guest" },
        200,
        [expiredSessionCookie(AUTH_STATE_ADMIN)]
      );
    }
    if (resource === "admin/links") {
      return await createLink(context.request, context.env);
    }
    if (resource === "admin/links/revoke") {
      return await revokeLink(context.request, context.env);
    }
    if (resource === "secret/exchange") {
      return await exchangeSecretLink(context.request, context.env);
    }
    if (resource === "authenticated/logout") {
      return jsonResponse(
        { state: "guest" },
        200,
        [
          expiredSessionCookie(AUTH_STATE_AUTHENTICATED),
          expiredSessionCookie(AUTH_STATE_ADMIN),
        ]
      );
    }
    return jsonResponse({ error: "지원하지 않는 경로입니다." }, 404);
  } catch (error) {
    if (error instanceof RequestError) {
      return jsonResponse({ error: error.message }, error.status, error.cookies);
    }
    if (error instanceof AuthConfigurationError) {
      console.error("Authentication configuration failed", error.message);
      return jsonResponse({ error: "인증 서비스를 사용할 수 없습니다." }, 503);
    }
    console.error("Authentication API failed", error);
    return jsonResponse({ error: "요청을 처리하지 못했습니다." }, 500);
  }
}
