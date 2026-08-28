import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

if (!globalThis.crypto) {
  Object.defineProperty(globalThis, "crypto", { value: webcrypto });
}
if (!globalThis.btoa) {
  Object.defineProperty(globalThis, "btoa", {
    value: (value) => Buffer.from(value, "binary").toString("base64"),
  });
}
if (!globalThis.atob) {
  Object.defineProperty(globalThis, "atob", {
    value: (value) => Buffer.from(value, "base64").toString("binary"),
  });
}

const authSource = await readFile(
  new URL("../functions/api/_auth.js", import.meta.url),
  "utf8"
);
const authDataUrl = `data:text/javascript;base64,${Buffer.from(authSource).toString("base64")}`;
const auth = await import(authDataUrl);
const routeSource = (await readFile(
  new URL("../functions/api/auth/[[path]].js", import.meta.url),
  "utf8"
)).replace('"../_auth.js"', `"${authDataUrl}"`);
const api = await import(
  `data:text/javascript;base64,${Buffer.from(routeSource).toString("base64")}`
);

const ADMIN_PASSWORD = "test-only-password-2026";
const SESSION_SECRET = auth.encodeBase64Url(new Uint8Array(32).fill(23));

async function createPasswordVerifier(password) {
  const iterations = 100000;
  const salt = new Uint8Array(16).fill(11);
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(password),
    "PBKDF2",
    false,
    ["deriveBits"]
  );
  const derived = new Uint8Array(await crypto.subtle.deriveBits(
    { name: "PBKDF2", hash: "SHA-256", salt, iterations },
    key,
    256
  ));
  return `pbkdf2-sha256$${iterations}$${auth.encodeBase64Url(salt)}$${auth.encodeBase64Url(derived)}`;
}

const ADMIN_VERIFIER = await createPasswordVerifier(ADMIN_PASSWORD);

function compact(sql) {
  return sql.replace(/\s+/gu, " ").trim();
}

class MockStatement {
  constructor(db, sql) {
    this.db = db;
    this.sql = compact(sql);
    this.values = [];
  }

  bind(...values) {
    this.values = values;
    return this;
  }

  async all() {
    return { results: this.db.all(this.sql, this.values) };
  }
}

class MockDatabase {
  constructor() {
    this.links = [];
    this.loginLimits = new Map();
    this.archiveFilters = new Map();
    this.archives = [
      "dcinside-singularity",
      "dcinside-agent-stack",
      "dcinside-zeus-pride",
      "fmkorea-munich",
      "game-news",
    ];
  }

  prepare(sql) {
    return new MockStatement(this, sql);
  }

  all(sql, values) {
    if (sql.includes("FROM auth_secret_link_archive_filters") && sql.includes("LIMIT 1")) {
      const row = this.archiveFilters.get(Number(values[0]));
      return row ? [{ ...row }] : [];
    }
    if (sql.startsWith("INSERT INTO auth_secret_link_archive_filters")) {
      const row = {
        excluded_archive_keys_json: values[1],
        updated_at: values[2],
      };
      this.archiveFilters.set(Number(values[0]), row);
      return [{ ...row }];
    }
    if (sql.includes("SELECT archive_key FROM archives WHERE is_public = 1")) {
      return this.archives.map((archiveKey) => ({ archive_key: archiveKey }));
    }
    if (sql.includes("FROM auth_login_limits") && sql.includes("LIMIT 1")) {
      const row = this.loginLimits.get(values[0]);
      return row ? [{ ...row }] : [];
    }
    if (sql.startsWith("INSERT INTO auth_login_limits")) {
      this.loginLimits.set(values[0], {
        client_key_hash: values[0],
        failure_count: values[1],
        window_started_at: values[2],
        locked_until: values[3],
        updated_at: values[4],
      });
      return [{ client_key_hash: values[0] }];
    }
    if (sql.startsWith("DELETE FROM auth_login_limits")) {
      const existed = this.loginLimits.delete(values[0]);
      return existed ? [{ client_key_hash: values[0] }] : [];
    }
    if (sql.startsWith("INSERT INTO auth_secret_links")) {
      const row = {
        id: this.links.length + 1,
        label: values[0],
        token_hash: values[1],
        created_at: values[2],
        last_used_at: null,
        expires_at: values[3],
        revoked_at: null,
      };
      this.links.push(row);
      return [{ ...row }];
    }
    if (sql.includes("FROM auth_secret_links") && sql.includes("ORDER BY id DESC")) {
      return [...this.links].reverse().map((row) => ({ ...row }));
    }
    if (sql.startsWith("UPDATE auth_secret_links SET revoked_at")) {
      const row = this.links.find((item) => item.id === Number(values[1]));
      if (!row) {
        return [];
      }
      row.revoked_at ||= values[0];
      return [{ id: row.id }];
    }
    if (sql.startsWith("UPDATE auth_secret_links SET last_used_at")) {
      const [lastUsedAt, tokenHash, nowIso] = values;
      const row = this.links.find((item) => (
        item.token_hash === tokenHash
        && item.revoked_at === null
        && (item.expires_at === null || item.expires_at > nowIso)
      ));
      if (!row) {
        return [];
      }
      row.last_used_at = lastUsedAt;
      return [{ id: row.id }];
    }
    if (sql.includes("FROM auth_secret_links") && sql.includes("WHERE id = ?")) {
      const [id, nowIso] = values;
      const row = this.links.find((item) => (
        item.id === Number(id)
        && item.revoked_at === null
        && (item.expires_at === null || item.expires_at > nowIso)
      ));
      return row ? [{ id: row.id }] : [];
    }
    throw new Error(`Unexpected SQL: ${sql}`);
  }
}

function testEnv(db) {
  return {
    DB: db,
    TC_AUTH_ADMIN_VERIFIER: ADMIN_VERIFIER,
    TC_AUTH_SESSION_SECRET: SESSION_SECRET,
  };
}

function request(resource, {
  method = "GET",
  body,
  cookie,
  db = new MockDatabase(),
  sameOrigin = true,
  env = testEnv(db),
} = {}) {
  const headers = new Headers({ Accept: "application/json" });
  if (cookie) {
    headers.set("Cookie", cookie);
  }
  if (body !== undefined) {
    headers.set("Content-Type", "application/json");
  }
  if (method === "POST" && sameOrigin) {
    headers.set("Origin", "https://todaycommunity.example");
    headers.set("Sec-Fetch-Site", "same-origin");
    headers.set("X-TodayCommunity-Auth", "1");
  }
  headers.set("CF-Connecting-IP", "203.0.113.7");
  const req = new Request(`https://todaycommunity.example/api/auth/${resource}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (method === "POST" ? api.onRequestPost : api.onRequestGet)({
    request: req,
    env,
  });
}

function cookieFromResponse(response, name) {
  const match = new RegExp(`${name}=([^;,]+)`, "u")
    .exec(String(response.headers.get("set-cookie") || ""));
  assert.ok(match, `Expected ${name} in Set-Cookie`);
  return `${name}=${match[1]}`;
}

test("reports guest state and protects the admin link manager", async () => {
  const db = new MockDatabase();
  const session = await request("session", { db });
  assert.equal(session.status, 200);
  assert.deepEqual(await session.json(), {
    state: "guest",
    authenticated: false,
    capabilities: {
      rate: false,
      hide: false,
      manage_rules: false,
      manage_auth: false,
    },
  });
  assert.match(session.headers.get("cache-control"), /no-store/u);

  const links = await request("admin/links", { db });
  assert.equal(links.status, 401);
});

test("logs in the single admin and enforces the same-origin write contract", async () => {
  const db = new MockDatabase();
  const rejected = await request("admin/login", {
    method: "POST",
    body: { password: ADMIN_PASSWORD },
    db,
    sameOrigin: false,
  });
  assert.equal(rejected.status, 403);

  const wrong = await request("admin/login", {
    method: "POST",
    body: { password: `${ADMIN_PASSWORD}-wrong` },
    db,
  });
  assert.equal(wrong.status, 401);
  assert.equal(db.loginLimits.size, 1);

  const login = await request("admin/login", {
    method: "POST",
    body: { password: ADMIN_PASSWORD },
    db,
  });
  assert.equal(login.status, 200);
  const adminCookie = cookieFromResponse(login, auth.ADMIN_COOKIE);
  assert.match(login.headers.get("set-cookie"), /HttpOnly/u);
  assert.match(login.headers.get("set-cookie"), /Secure/u);
  assert.match(login.headers.get("set-cookie"), /SameSite=Strict/u);
  assert.equal(db.loginLimits.size, 0);

  const session = await request("session", { db, cookie: adminCookie });
  assert.equal((await session.json()).state, "admin");
});

test("rejects verifier costs above the Cloudflare Pages PBKDF2 limit", async () => {
  const db = new MockDatabase();
  const originalConsoleError = console.error;
  console.error = () => {};
  try {
    const response = await request("admin/login", {
      method: "POST",
      body: { password: ADMIN_PASSWORD },
      db,
      env: {
        ...testEnv(db),
        TC_AUTH_ADMIN_VERIFIER: ADMIN_VERIFIER.replace("$100000$", "$600000$"),
      },
    });
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), {
      error: "인증 서비스를 사용할 수 없습니다.",
    });
    assert.equal(db.loginLimits.size, 0);
  } finally {
    console.error = originalConsoleError;
  }
});

test("issues, exchanges, lists, and revokes one-time secret links", async () => {
  const db = new MockDatabase();
  const login = await request("admin/login", {
    method: "POST",
    body: { password: ADMIN_PASSWORD },
    db,
  });
  const adminCookie = cookieFromResponse(login, auth.ADMIN_COOKIE);

  const created = await request("admin/links", {
    method: "POST",
    body: { label: "집 PC", expires_in_days: 0 },
    cookie: adminCookie,
    db,
  });
  assert.equal(created.status, 201);
  const createdPayload = await created.json();
  const secretUrl = new URL(createdPayload.secret_url);
  assert.equal(secretUrl.pathname, "/owner/");
  assert.equal(secretUrl.searchParams.get("next"), "/?target=all");
  const rawToken = new URLSearchParams(secretUrl.hash.slice(1)).get("token");
  assert.match(rawToken, /^[A-Za-z0-9_-]{43}$/u);
  assert.equal(db.links.length, 1);
  assert.notEqual(db.links[0].token_hash, rawToken);
  assert.equal(db.links[0].token_hash.length, 64);

  const list = await request("admin/links", { db, cookie: adminCookie });
  const listPayload = await list.json();
  assert.equal(listPayload.items[0].label, "집 PC");
  assert.equal(Object.hasOwn(listPayload.items[0], "token_hash"), false);

  const exchanged = await request("secret/exchange", {
    method: "POST",
    body: { token: rawToken },
    db,
  });
  assert.equal(exchanged.status, 200);
  const authenticatedCookie = cookieFromResponse(
    exchanged,
    auth.AUTHENTICATED_COOKIE
  );
  assert.equal((await exchanged.json()).state, "authenticated");

  const authenticatedSession = await request("session", {
    db,
    cookie: authenticatedCookie,
  });
  assert.equal((await authenticatedSession.json()).state, "authenticated");

  const managerDenied = await request("admin/links", {
    db,
    cookie: authenticatedCookie,
  });
  assert.equal(managerDenied.status, 401);

  const revoked = await request("admin/links/revoke", {
    method: "POST",
    body: { id: db.links[0].id },
    cookie: adminCookie,
    db,
  });
  assert.equal(revoked.status, 200);
  assert.equal((await revoked.json()).items[0].active, false);

  const staleSession = await request("session", {
    db,
    cookie: authenticatedCookie,
  });
  assert.equal((await staleSession.json()).state, "guest");
  assert.match(staleSession.headers.get("set-cookie"), /Max-Age=0/u);

  const reused = await request("secret/exchange", {
    method: "POST",
    body: { token: rawToken },
    db,
  });
  assert.equal(reused.status, 401);
});

test("stores an isolated archive filter for each active secret link", async () => {
  const db = new MockDatabase();
  const guestRead = await request("archive-filters", { db });
  assert.equal(guestRead.status, 401);

  const login = await request("admin/login", {
    method: "POST",
    body: { password: ADMIN_PASSWORD },
    db,
  });
  const adminCookie = cookieFromResponse(login, auth.ADMIN_COOKIE);
  const adminRead = await request("archive-filters", { db, cookie: adminCookie });
  assert.equal(adminRead.status, 401);

  async function issueAuthenticatedCookie(label) {
    const created = await request("admin/links", {
      method: "POST",
      body: { label, expires_in_days: 0 },
      cookie: adminCookie,
      db,
    });
    const secretUrl = new URL((await created.json()).secret_url);
    const token = new URLSearchParams(secretUrl.hash.slice(1)).get("token");
    const exchanged = await request("secret/exchange", {
      method: "POST",
      body: { token },
      db,
    });
    return cookieFromResponse(exchanged, auth.AUTHENTICATED_COOKIE);
  }

  const firstCookie = await issueAuthenticatedCookie("첫 번째 링크");
  const firstDefault = await request("archive-filters", {
    db,
    cookie: firstCookie,
  });
  assert.deepEqual(await firstDefault.json(), {
    excluded_archive_keys: [],
    updated_at: null,
  });

  const saved = await request("archive-filters", {
    method: "POST",
    body: {
      excluded_archive_keys: [
        "game-news",
        "dcinside-agent-stack",
        "game-news",
      ],
    },
    cookie: firstCookie,
    db,
  });
  assert.equal(saved.status, 200);
  assert.deepEqual((await saved.json()).excluded_archive_keys, [
    "dcinside-agent-stack",
    "game-news",
  ]);

  const firstReload = await request("archive-filters", {
    db,
    cookie: firstCookie,
  });
  assert.deepEqual((await firstReload.json()).excluded_archive_keys, [
    "dcinside-agent-stack",
    "game-news",
  ]);

  const secondCookie = await issueAuthenticatedCookie("두 번째 링크");
  const secondDefault = await request("archive-filters", {
    db,
    cookie: secondCookie,
  });
  assert.deepEqual((await secondDefault.json()).excluded_archive_keys, []);

  const invalid = await request("archive-filters", {
    method: "POST",
    body: { excluded_archive_keys: ["private-or-missing"] },
    cookie: firstCookie,
    db,
  });
  assert.equal(invalid.status, 400);
  assert.deepEqual(
    JSON.parse(db.archiveFilters.get(1).excluded_archive_keys_json),
    ["dcinside-agent-stack", "game-news"]
  );

  const revoked = await request("admin/links/revoke", {
    method: "POST",
    body: { id: 1 },
    cookie: adminCookie,
    db,
  });
  assert.equal(revoked.status, 200);
  const revokedRead = await request("archive-filters", {
    db,
    cookie: firstCookie,
  });
  assert.equal(revokedRead.status, 401);
});

test("locks repeated wrong-password attempts", async () => {
  const db = new MockDatabase();
  let response;
  for (let attempt = 0; attempt < 5; attempt += 1) {
    response = await request("admin/login", {
      method: "POST",
      body: { password: `${ADMIN_PASSWORD}-wrong` },
      db,
    });
  }
  assert.equal(response.status, 429);

  const correctButLocked = await request("admin/login", {
    method: "POST",
    body: { password: ADMIN_PASSWORD },
    db,
  });
  assert.equal(correctButLocked.status, 429);
});

test("fails closed when authentication secrets are not configured", async () => {
  const originalConsoleError = console.error;
  console.error = () => {};
  try {
    const response = await request("session", {
      env: { DB: new MockDatabase() },
    });
    assert.equal(response.status, 503);
  } finally {
    console.error = originalConsoleError;
  }
});
