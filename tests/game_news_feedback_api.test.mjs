import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFile } from "node:fs/promises";
import test from "node:test";

const authSource = await readFile(
  new URL("../functions/api/_auth.js", import.meta.url),
  "utf8"
);
const authDataUrl = `data:text/javascript;base64,${Buffer.from(authSource).toString("base64")}`;
const auth = await import(authDataUrl);
const source = (await readFile(
  new URL("../functions/api/game-news/[[path]].js", import.meta.url),
  "utf8"
)).replace('"../_auth.js"', `"${authDataUrl}"`);
const api = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);
const sessionSecret = auth.encodeBase64Url(new Uint8Array(32).fill(7));
const adminToken = await auth.createSessionToken(
  { TC_AUTH_SESSION_SECRET: sessionSecret },
  auth.AUTH_STATE_ADMIN
);

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

  async run() {
    return this.db.run(this.sql, this.values);
  }
}

class MockDatabase {
  constructor() {
    this.postKey = "a".repeat(32);
    this.urlHash = "b".repeat(64);
    this.post = {
      id: 1,
      external_post_id: this.postKey,
      canonical_post_key: `game-news:${this.urlHash}`,
      archive_key: "game-news",
      status: "active",
      title: "테스트 게임 기사",
      post_url: "https://example.com/article",
      subject: "industry",
      last_seen_at: "2026-08-15T00:00:00Z",
    };
    this.candidate = {
      id: 7,
      url_sha256: this.urlHash,
      current_evaluation_id: 9,
    };
    this.feedback = [];
    this.visibility = [];
    this.rules = [];
  }

  prepare(sql) {
    return new MockStatement(this, sql);
  }

  all(sql, values) {
    if (sql.includes("FROM posts AS p") && sql.includes("LIMIT 2")) {
      if (values[0] !== this.post.external_post_id) return [];
      return [{
        post_id: this.post.id,
        post_key: this.post.external_post_id,
        status: this.post.status,
        title: this.post.title,
        post_url: this.post.post_url,
        subject: this.post.subject,
        last_seen_at: this.post.last_seen_at,
        candidate_id: this.candidate.id,
        evaluation_id: this.candidate.current_evaluation_id,
      }];
    }
    if (sql.includes("LEFT JOIN game_news_feedback AS f")) {
      const actor = values[0];
      return values.slice(1).filter((key) => key === this.post.external_post_id).map((key) => {
        const latest = this.feedback.filter(
          (entry) => entry.candidate_id === this.candidate.id && entry.actor === actor
        ).at(-1);
        return {
          post_key: key,
          rating_level: latest?.feedback_type === "clear" ? null : latest?.rating_level ?? null,
          feedback_version: latest?.id ?? 0,
          reason_code: latest?.reason_code ?? null,
          hidden: this.post.status === "hidden" ? 1 : 0,
        };
      });
    }
    if (sql.includes("FROM game_news_feedback WHERE idempotency_key")) {
      return this.feedback.filter((entry) => entry.idempotency_key === values[0]);
    }
    if (sql.startsWith("INSERT INTO game_news_feedback")) {
      if (this.feedback.some((entry) => entry.idempotency_key === values[7])) return [];
      const row = {
        id: this.feedback.length + 1,
        candidate_id: values[0],
        evaluation_id: values[1],
        feedback_type: values[2],
        rating_level: values[3],
        reason_code: values[4],
        note: values[5],
        actor: values[6],
        idempotency_key: values[7],
        created_at: values[8],
      };
      this.feedback.push(row);
      return [{ id: row.id }];
    }
    if (sql.includes("FROM posts") && sql.includes("status = 'hidden'")) {
      return this.post.status === "hidden" ? [{
        post_key: this.post.external_post_id,
        title: this.post.title,
        post_url: this.post.post_url,
        subject: this.post.subject,
        last_seen_at: this.post.last_seen_at,
      }] : [];
    }
    if (sql.includes("FROM game_news_visibility_events WHERE idempotency_key")) {
      return this.visibility.filter((entry) => entry.idempotency_key === values[0]);
    }
    if (sql.startsWith("INSERT INTO game_news_visibility_events")) {
      if (this.visibility.some((entry) => entry.idempotency_key === values[4])) return [];
      const row = {
        id: this.visibility.length + 1,
        candidate_id: values[0],
        evaluation_id: values[1],
        action: values[2],
        actor: values[3],
        idempotency_key: values[4],
        created_at: values[5],
      };
      this.visibility.push(row);
      return [{ id: row.id }];
    }
    if (sql.includes("FROM game_news_manual_rule_events AS r")) {
      const latest = this.rules.filter((entry) => entry.rule_key === values[0]).at(-1);
      return latest ? [{
        rule_event_id: latest.id,
        action: latest.action,
        rule_text: latest.rule_text,
        created_at: latest.created_at,
      }] : [];
    }
    if (sql.includes("FROM game_news_manual_rule_events WHERE idempotency_key")) {
      return this.rules.filter((entry) => entry.idempotency_key === values[0]);
    }
    if (sql.startsWith("INSERT INTO game_news_manual_rule_events")) {
      if (this.rules.some((entry) => entry.idempotency_key === values[5])) return [];
      const currentVersion = this.rules
        .filter((entry) => entry.rule_key === values[7])
        .at(-1)?.id ?? 0;
      if (currentVersion !== values[8]) return [];
      const row = {
        id: this.rules.length + 1,
        rule_key: values[0],
        action: values[1],
        rule_text: values[2],
        strength: values[3],
        actor: values[4],
        idempotency_key: values[5],
        created_at: values[6],
      };
      this.rules.push(row);
      return [{ id: row.id }];
    }
    throw new Error(`Unexpected SQL: ${sql}`);
  }

  run(sql, values) {
    if (sql.startsWith("UPDATE posts SET status")) {
      assert.equal(values[1], this.post.id);
      this.post.status = values[0];
      return { success: true, meta: { changes: 1 } };
    }
    throw new Error(`Unexpected run SQL: ${sql}`);
  }
}

function request(resource, {
  method = "GET",
  body,
  headers = {},
  db,
  authenticated = true,
} = {}) {
  const requestHeaders = new Headers({ accept: "application/json", ...headers });
  if (authenticated) {
    requestHeaders.set("Cookie", `${auth.ADMIN_COOKIE}=${adminToken}`);
  }
  if (body !== undefined) {
    requestHeaders.set("Content-Type", "application/json");
  }
  const req = new Request(`https://todaycommunity.example/api/game-news/${resource}`, {
    method,
    headers: requestHeaders,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (method === "POST" ? api.onRequestPost : api.onRequestGet)({
    request: req,
    env: { DB: db, TC_AUTH_SESSION_SECRET: sessionSecret },
  });
}

async function body(response) {
  return response.json();
}

const writeHeaders = {
  Origin: "https://todaycommunity.example",
  "Sec-Fetch-Site": "same-origin",
  "X-TodayCommunity-Write": "1",
};

test("exposes guest and admin states through one authentication boundary", async () => {
  const guestResponse = await request("session", { authenticated: false });
  assert.equal(guestResponse.status, 200);
  const guest = await body(guestResponse);
  assert.equal(guest.authentication, "guest");
  assert.equal(guest.actor, null);
  assert.equal(guest.capabilities.rate, false);

  const response = await request("session");
  assert.equal(response.status, 200);
  const payload = await body(response);
  assert.equal(payload.authentication, "admin");
  assert.equal(payload.actor, "owner:primary-v1");
  assert.deepEqual(payload.capabilities, {
    rate: true,
    hide: true,
    manage_rules: true,
    manage_auth: true,
  });
  assert.match(response.headers.get("cache-control"), /no-store/);
});

test("denies owner reads and writes to guests", async () => {
  const db = new MockDatabase();
  const readResponse = await request("feedback", { db, authenticated: false });
  assert.equal(readResponse.status, 401);

  const writeResponse = await request("feedback", {
    method: "POST",
    body: {
      post_key: db.postKey,
      rating_level: 1,
      idempotency_key: "feedback:guest-denied",
    },
    headers: writeHeaders,
    db,
    authenticated: false,
  });
  assert.equal(writeResponse.status, 401);
  assert.equal(db.feedback.length, 0);

  const preferenceRead = await request("preferences", { db, authenticated: false });
  assert.equal(preferenceRead.status, 401);
  const preferenceWrite = await request("preferences", {
    method: "POST",
    body: {
      content: "LCK 기사는 큰 사건만 수집한다.",
      base_version: 0,
      idempotency_key: "preference:guest-denied",
    },
    headers: writeHeaders,
    db,
    authenticated: false,
  });
  assert.equal(preferenceWrite.status, 401);
  assert.equal(db.rules.length, 0);
});

test("records four-level feedback and handles exact idempotent retries", async () => {
  const db = new MockDatabase();
  const feedbackBody = {
    post_key: db.postKey,
    rating_level: -2,
    reason_code: null,
    actor: "client-supplied-actor-is-ignored",
    idempotency_key: "feedback:1234567890abcdef",
  };
  const first = await request("feedback", {
    method: "POST", body: feedbackBody, headers: writeHeaders, db,
  });
  assert.equal(first.status, 201);
  assert.equal((await body(first)).item.rating_level, -2);
  assert.equal(db.feedback[0].actor, "owner:primary-v1");
  const retry = await request("feedback", {
    method: "POST", body: feedbackBody, headers: writeHeaders, db,
  });
  assert.equal(retry.status, 201);
  assert.equal(db.feedback.length, 1);

  const conflict = await request("feedback", {
    method: "POST",
    body: { ...feedbackBody, rating_level: 2 },
    headers: writeHeaders,
    db,
  });
  assert.equal(conflict.status, 409);
});

test("requires the explicit same-origin write contract", async () => {
  const db = new MockDatabase();
  const response = await request("feedback", {
    method: "POST",
    body: {
      post_key: db.postKey,
      rating_level: 1,
      idempotency_key: "feedback:missing-header",
    },
    db,
  });
  assert.equal(response.status, 403);
  assert.equal(db.feedback.length, 0);
});

test("keeps X visibility separate, reversible, and globally projected", async () => {
  const db = new MockDatabase();
  const hidden = await request("visibility", {
    method: "POST",
    body: {
      post_key: db.postKey,
      action: "hide",
      idempotency_key: "visibility:hide:123456",
    },
    headers: writeHeaders,
    db,
  });
  assert.equal(hidden.status, 201);
  assert.equal(db.post.status, "hidden");
  assert.equal(db.feedback.length, 0);
  assert.equal((await body(await request("hidden", { db }))).items.length, 1);

  const restored = await request("visibility", {
    method: "POST",
    body: {
      post_key: db.postKey,
      action: "restore",
      idempotency_key: "visibility:restore:1234",
    },
    headers: writeHeaders,
    db,
  });
  assert.equal(restored.status, 201);
  assert.equal(db.post.status, "active");
  assert.equal((await body(await request("hidden", { db }))).items.length, 0);
});

test("loads, versions, clears, and conflict-checks one preference document", async () => {
  const db = new MockDatabase();
  const emptyResponse = await request("preferences", { db });
  assert.equal(emptyResponse.status, 200);
  assert.deepEqual((await body(emptyResponse)).document, {
    content: "",
    version: 0,
    updated_at: null,
    max_length: 1000,
  });

  const setBody = {
    content: "LCK 기사는 큰 사건만 수집해줘.",
    base_version: 0,
    idempotency_key: "preference:set:1234567890",
  };
  const setResponse = await request("preferences", {
    method: "POST",
    body: setBody,
    headers: writeHeaders,
    db,
  });
  assert.equal(setResponse.status, 201);
  const setDocument = (await body(setResponse)).document;
  assert.equal(setDocument.content, "LCK 기사는 큰 사건만 수집해줘.");
  assert.equal(setDocument.version, 1);
  assert.equal(db.rules[0].rule_key, "owner-preferences-document-v1");
  assert.equal(db.rules[0].actor, "owner:primary-v1");

  const retryResponse = await request("preferences", {
    method: "POST",
    body: setBody,
    headers: writeHeaders,
    db,
  });
  assert.equal(retryResponse.status, 201);
  assert.equal((await body(retryResponse)).document.version, 1);
  assert.equal(db.rules.length, 1);

  const oversizedResponse = await request("preferences", {
    method: "POST",
    body: {
      content: "가".repeat(1001),
      base_version: 1,
      idempotency_key: "preference:oversized:1234",
    },
    headers: writeHeaders,
    db,
  });
  assert.equal(oversizedResponse.status, 400);
  assert.equal(db.rules.length, 1);

  const editResponse = await request("preferences", {
    method: "POST",
    body: {
      content: "LCK를 포함한 e스포츠 기사는 큰 사건만 수집해줘.",
      base_version: 1,
      idempotency_key: "preference:edit:12345678",
    },
    headers: writeHeaders,
    db,
  });
  assert.equal(editResponse.status, 201);
  assert.equal((await body(editResponse)).document.version, 2);

  const conflictResponse = await request("preferences", {
    method: "POST",
    body: {
      content: "오래된 화면의 덮어쓰기",
      base_version: 1,
      idempotency_key: "preference:stale:1234567",
    },
    headers: writeHeaders,
    db,
  });
  assert.equal(conflictResponse.status, 409);
  assert.match((await body(conflictResponse)).error, /다른 기기/);
  assert.equal(db.rules.length, 2);

  const clearResponse = await request("preferences", {
    method: "POST",
    body: {
      content: "   ",
      base_version: 2,
      idempotency_key: "preference:clear:123456",
    },
    headers: writeHeaders,
    db,
  });
  assert.equal(clearResponse.status, 201);
  assert.equal((await body(clearResponse)).document.content, "");
  assert.equal((await body(await request("preferences", { db }))).document.version, 3);
});
