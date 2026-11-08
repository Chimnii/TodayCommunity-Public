import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

const authSource = await readFile(new URL("../functions/api/_auth.js", import.meta.url), "utf8");
const authUrl = `data:text/javascript;base64,${Buffer.from(authSource).toString("base64")}`;
const auth = await import(authUrl);
const source = (await readFile(new URL("../functions/api/ai-practice/[[path]].js", import.meta.url), "utf8"))
  .replace('"../_auth.js"', `"${authUrl}"`);
const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const migrations = await Promise.all(["020_ai_practice.sql", "021_ai_practice_backlog.sql"]
  .map(name => readFile(new URL(`../cloudflare/migrations/${name}`, import.meta.url), "utf8")));
const secret = auth.encodeBase64Url(new Uint8Array(32).fill(39));
const env = { TC_AUTH_SESSION_SECRET: secret };
const admin = await auth.createSessionToken(env, auth.AUTH_STATE_ADMIN);
const link = await auth.createSessionToken(env, auth.AUTH_STATE_AUTHENTICATED, 1);
const privateText = "PRIVATE-AI-POLICY: 원문 근거와 재사용할 배움을 확인한다. h_<hex10>";
const hash = value => createHash("sha256").update(value).digest("hex");
const validityVersion = source.match(/const VALIDITY_VERSION = "([^"]+)"/u)[1];

function validityFixture() {
  const now = Date.now();
  return { version: validityVersion, checked_at: new Date(now - 60_000).toISOString(),
    expires_at: new Date(now + 3600_000).toISOString(), claims: [], documents: [],
    assessment: { status: "timeless", reason: "The fixture tests a reusable design principle.", citations: [] } };
}

function database(t) {
  const sqlite = new DatabaseSync(":memory:");
  t.after(() => sqlite.close());
  sqlite.exec(`CREATE TABLE archives(archive_key TEXT PRIMARY KEY,display_name TEXT,description TEXT,display_order INTEGER,is_public INTEGER,content_kind TEXT);
    CREATE TABLE sources(source_key TEXT PRIMARY KEY,archive_key TEXT,site_name TEXT,board_name TEXT,board_url TEXT);
    CREATE TABLE archive_stats(archive_key TEXT PRIMARY KEY,active_post_count INTEGER,latest_seen_at TEXT,subject_options_json TEXT,stats_version INTEGER,updated_at TEXT);
    CREATE TABLE posts(id INTEGER PRIMARY KEY,archive_key TEXT,status TEXT,title TEXT);
    CREATE TABLE auth_secret_links(id INTEGER PRIMARY KEY,revoked_at TEXT,expires_at TEXT);
    INSERT INTO auth_secret_links VALUES(1,NULL,NULL);`);
  sqlite.exec(migrations[0]);
  const insert = sqlite.prepare(`INSERT INTO ai_practice_candidates(item_id,source_key,canonical_url,content_hash,
    instruction_version,case_key,original_title,title,summary,subject,evidence_url,evidence_level,content_kind,
    published_at_precision,first_seen_at,last_seen_at,decision,post_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`);
  for (const [index, decision] of ["include", "exclude", "review"].entries()) {
    sqlite.prepare("INSERT INTO posts(id,archive_key,status,title) VALUES(?,?,?,?)").run(index + 1, "ai-practice", ["active", "hidden", "deleted"][index], "public article");
    insert.run(`item-${index}`, "ai-practice-hermes", `https://example.com/${index}`, "body-hash", "v6", `case-${index}`,
      "original", "title", "summary", "프로젝트 운영", `https://example.com/${index}`, "full_text", "experience",
      "second", "2026-11-01T00:00:00Z", "2026-11-01T00:00:00Z", decision, index + 1);
  }
  sqlite.exec(migrations[1].replace(/\r\n/g, "\n"));
  sqlite.exec(`ALTER TABLE ai_practice_candidates ADD COLUMN validity_json TEXT CHECK(validity_json IS NULL OR json_valid(validity_json));
    ALTER TABLE ai_practice_candidates ADD COLUMN validity_checked_at TEXT;
    ALTER TABLE ai_practice_candidates ADD COLUMN validity_expires_at TEXT;
    ALTER TABLE ai_practice_candidates ADD COLUMN validity_version TEXT;`);
  const validity = validityFixture();
  sqlite.prepare(`UPDATE ai_practice_candidates SET validity_json=?,validity_checked_at=?,validity_expires_at=?,validity_version=?`)
    .run(JSON.stringify(validity), validity.checked_at, validity.expires_at, validity.version);
  sqlite.exec(`UPDATE ai_practice_queue SET status='processed', processed_policy_revision=1,
    processed_policy_hash=(SELECT policy_hash FROM ai_practice_policy_state WHERE id=1), processed_instruction_version='ai-practice-v9';
    UPDATE ai_practice_candidates SET policy_revision=1,instruction_version='ai-practice-v9',
      policy_hash=(SELECT policy_hash FROM ai_practice_policy_state WHERE id=1);
    INSERT INTO ai_practice_discovery_state(source_key,lane,status,complete,discovered_count,last_discovered_at,cursor_json,scope_json)
      VALUES('ai-practice-hermes','recent','ok',1,3,'2026-11-07T00:00:00Z','{"head":1}','{"kind":"recent"}'),
            ('ai-practice-hermes','backfill','partial',0,3,'2026-11-07T00:00:00Z','{"page":2}','{"kind":"archive"}');`);
  const db = { sqlite, queries: [], beforeBatch: null, failAt: null,
    prepare(sql) {
      return { sql, values: [], bind(...values) { this.values = values; return this; },
        async all() { db.queries.push(sql); return { results: sqlite.prepare(sql).all(...this.values) }; } };
    },
    async batch(statements) {
      if (this.beforeBatch) { const before = this.beforeBatch; this.beforeBatch = null; await before(); }
      sqlite.exec("BEGIN IMMEDIATE");
      try {
        const results = statements.map((statement, index) => {
          if (index === this.failAt) throw new Error("internal database details must stay private");
          this.queries.push(statement.sql);
          const result = sqlite.prepare(statement.sql).run(...statement.values);
          return { results: [], meta: { changes: Number(result.changes) } };
        });
        sqlite.exec("COMMIT");
        return results;
      } catch (error) { sqlite.exec("ROLLBACK"); throw error; }
    } };
  return db;
}

function request(db, { method = "GET", identity = "admin", body, headers = {}, path = "rules" } = {}) {
  const allHeaders = { ...headers };
  if (identity === "admin") allHeaders.Cookie = `__Host-tc_admin=${admin}`;
  if (identity === "link") allHeaders.Cookie = `__Host-tc_authenticated=${link}`;
  if (method === "POST") Object.assign(allHeaders, { "Content-Type": "application/json", Origin: "https://todaycommunity.example",
    "Sec-Fetch-Site": "same-origin", "X-TodayCommunity-Write": "1", ...headers });
  const input = new Request(`https://todaycommunity.example/api/ai-practice/${path}`, { method, headers: allHeaders,
    body: method === "POST" ? JSON.stringify(body) : undefined });
  return api[`onRequest${method[0]}${method.slice(1).toLowerCase()}`]({ request: input, env: { ...env, DB: db } });
}
const saveBody = (event = "test-save-event-0001", content = privateText, base_revision = 1) => ({ event_id: event, content, base_revision });
const current = db => db.sqlite.prepare("SELECT * FROM ai_practice_policy_state WHERE id=1").get();
const count = (db, table) => db.sqlite.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n;

test("criteria returns only the current document fields to valid secret-link and admin sessions", async t => {
  const db = database(t);
  const saved = await (await request(db, { method: "POST", body: saveBody() })).json();
  const expected = { content: privateText, revision: 2, updated_at: saved.document.updated_at };
  const beforeWrites = db.sqlite.prepare("SELECT total_changes() AS n").get().n;
  for (const identity of ["link", "admin"]) {
    db.queries.length = 0;
    const response = await request(db, { identity, path: "criteria" });
    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { document: expected });
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.equal(response.headers.get("vary"), "Cookie");
    assert.equal(response.headers.get("x-content-type-options"), "nosniff");
    assert.equal(response.headers.get("x-robots-tag"), "noindex, nofollow");
    assert.ok(db.queries.every(sql => !/ai_practice_(?:queue|candidates|discovery|source_state)/u.test(sql)), "criteria does not read application status");
    assert.equal(db.queries.filter(sql => sql.includes("ai_practice_policy_state")).length, 1);
  }
  assert.equal(db.sqlite.prepare("SELECT total_changes() AS n").get().n, beforeWrites);
  const updated = await (await request(db, { method: "POST", body: saveBody("criteria-latest-0001", "최신 선정 기준 전문", 2) })).json();
  const latest = await (await request(db, { identity: "link", path: "criteria" })).json();
  assert.deepEqual(latest, { document: { content: "최신 선정 기준 전문", revision: 3, updated_at: updated.document.updated_at } });
});

test("criteria rejects guests, forged or expired sessions, and revoked or expired secret links before policy access", async t => {
  const expiredLink = await auth.createSessionToken(env, auth.AUTH_STATE_AUTHENTICATED, 1, Date.now() - 31 * 24 * 60 * 60 * 1000);
  const expiredAdmin = await auth.createSessionToken(env, auth.AUTH_STATE_ADMIN, null, Date.now() - 2 * 60 * 60 * 1000);
  const [payload, signature] = link.split(".");
  const forged = `${payload}.${signature[0] === "A" ? "B" : "A"}${signature.slice(1)}`;
  const cases = [
    { name: "guest" },
    { name: "expired session", cookie: `__Host-tc_authenticated=${expiredLink}` },
    { name: "forged session", cookie: `__Host-tc_authenticated=${forged}` },
    { name: "expired admin", cookie: `__Host-tc_admin=${expiredAdmin}` },
    { name: "revoked link", cookie: `__Host-tc_authenticated=${link}`, sql: "UPDATE auth_secret_links SET revoked_at='2026-01-01T00:00:00Z'" },
    { name: "expired link", cookie: `__Host-tc_authenticated=${link}`, sql: "UPDATE auth_secret_links SET expires_at='2026-01-01T00:00:00Z'" },
    { name: "deleted link", cookie: `__Host-tc_authenticated=${link}`, sql: "DELETE FROM auth_secret_links" },
  ];
  for (const entry of cases) await t.test(entry.name, async sub => {
    const db = database(sub);
    if (entry.sql) db.sqlite.exec(entry.sql);
    const response = await request(db, { identity: "guest", path: "criteria", headers: entry.cookie ? { Cookie: entry.cookie } : {} });
    assert.equal(response.status, 401);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.equal(response.headers.get("vary"), "Cookie");
    assert.doesNotMatch(await response.text(), /PRIVATE-AI-POLICY|document|content|application|policy_hash/);
    assert.ok(db.queries.every(sql => !sql.includes("ai_practice")));
    if (entry.cookie) assert.match(response.headers.get("set-cookie"), /Max-Age=0/u);
  });
});

test("criteria preserves stale-cookie cleanup when a valid secret session still authorizes the read", async t => {
  const db = database(t);
  const response = await request(db, { identity: "guest", path: "criteria", headers: {
    Cookie: `__Host-tc_admin=invalid; __Host-tc_authenticated=${link}`,
  } });
  assert.equal(response.status, 200);
  assert.match(response.headers.get("set-cookie"), /__Host-tc_admin=;.*Max-Age=0/u);
  assert.deepEqual(Object.keys((await response.json()).document).sort(), ["content", "revision", "updated_at"]);
});

test("criteria is GET-only for both allowed identities and cannot append policy events", async t => {
  const db = database(t);
  const before = current(db);
  const beforeWrites = db.sqlite.prepare("SELECT total_changes() AS n").get().n;
  for (const identity of ["link", "admin"]) for (const method of ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]) {
    db.queries.length = 0;
    const response = await request(db, { identity, method, path: "criteria", body: saveBody() });
    assert.equal(response.status, 405, `${identity} ${method}`);
    assert.equal(response.headers.get("allow"), "GET");
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.equal(response.headers.get("vary"), "Cookie");
    assert.doesNotMatch(await response.text(), /PRIVATE-AI-POLICY|document|content/);
    assert.ok(db.queries.every(sql => !sql.includes("ai_practice")));
  }
  assert.deepEqual(current(db), before);
  assert.equal(count(db, "ai_practice_manual_rule_events"), 0);
  assert.equal(db.sqlite.prepare("SELECT total_changes() AS n").get().n, beforeWrites);
});

test("criteria fails closed on missing or corrupted policy without consulting queue tables", async t => {
  for (const sql of ["DELETE FROM ai_practice_policy_state", `INSERT INTO ai_practice_policy_snapshots
    SELECT 2,policy_hash,'corrupted policy',updated_at,'fixture-corruption' FROM ai_practice_policy_state;
    UPDATE ai_practice_policy_state SET policy_revision=2`]) {
    const db = database(t);
    db.sqlite.exec(sql);
    const response = await request(db, { identity: "link", path: "criteria" });
    assert.equal(response.status, 503);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.doesNotMatch(await response.text(), /corrupted policy|document|content/);
    assert.ok(db.queries.every(query => !/ai_practice_(?:queue|candidates|discovery|source_state)/u.test(query)));
  }
});

test("AI rules reject guests and secret-link owners before reading private policy or status", async t => {
  const db = database(t);
  assert.equal((await request(db, { method: "POST", body: saveBody() })).status, 201);
  for (const identity of ["guest", "link"]) for (const method of ["GET", "POST"]) {
    db.queries.length = 0;
    const response = await request(db, { identity, method, body: saveBody("denied-event-0001") });
    assert.equal(response.status, 401);
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.doesNotMatch(await response.text(), /PRIVATE-AI-POLICY|content|sources|cursor_json/);
    assert.ok(db.queries.every(sql => !sql.includes("ai_practice")), identity);
  }
});

test("admin reads the seeded full document and distinct per-source pending/backfill status", async t => {
  const db = database(t);
  db.sqlite.exec("UPDATE ai_practice_queue SET status='blocked',processed_policy_revision=0 WHERE item_id='item-0'; UPDATE ai_practice_queue SET status='oversized' WHERE item_id='item-1';");
  const response = await request(db);
  const result = await response.json();
  assert.equal(response.status, 200);
  assert.equal(result.document.hash, hash(result.document.content));
  assert.equal(result.document.max_length, 6000);
  assert.equal(result.application.total_count, 3);
  assert.equal(result.application.pending_count, 2);
  assert.equal(result.application.failed_count, 1);
  assert.equal(result.application.oversized_count, 1);
  assert.equal(result.application.stale_count, 1);
  const hermes = result.application.sources.filter(item => item.source_key === "ai-practice-hermes");
  assert.equal(hermes.length, 1, "recent and backfill lanes do not duplicate queue counts");
  assert.equal(hermes[0].backfill.status, "partial");
  assert.equal(hermes[0].backfill.cursor_json, '{"page":2}');
  db.sqlite.exec("UPDATE ai_practice_queue SET cache_invalidated=1,status='pending' WHERE item_id='item-2';");
  assert.equal((await (await request(db)).json()).application.stale_count, 2, "same-policy duplicate invalidation still needs reassessment");
});

test("policy change uses three writes and lazily reassesses every decision without rewriting queue or visibility", async t => {
  const db = database(t);
  const beforeQueue = db.sqlite.prepare("SELECT * FROM ai_practice_queue ORDER BY item_id").all();
  const beforeWrites = db.sqlite.prepare("SELECT total_changes() AS n").get().n;
  const response = await request(db, { method: "POST", body: saveBody() });
  const result = await response.json();
  assert.equal(response.status, 201);
  assert.equal(result.document.content, privateText);
  assert.equal(result.document.hash, hash(privateText));
  assert.equal(result.document.revision, 2);
  assert.equal(result.save.changed, true);
  assert.equal(result.application.reassessment_count, 3);
  assert.equal(result.application.pending_count, 3);
  assert.equal(result.application.stale_count, 3);
  assert.equal(db.sqlite.prepare("SELECT total_changes() AS n").get().n - beforeWrites, 3);
  assert.equal(count(db, "ai_practice_policy_snapshots"), 2);
  assert.equal(count(db, "ai_practice_manual_rule_events"), 1);
  assert.deepEqual(db.sqlite.prepare("SELECT decision FROM ai_practice_candidates ORDER BY item_id").all().map(x => x.decision), ["include", "exclude", "review"]);
  assert.deepEqual(db.sqlite.prepare("SELECT status FROM posts ORDER BY id").all().map(x => x.status), ["active", "hidden", "deleted"]);
  assert.deepEqual(db.sqlite.prepare("SELECT * FROM ai_practice_queue ORDER BY item_id").all(), beforeQueue);
  assert.ok(db.queries.every(sql => !/UPDATE\s+ai_practice_queue/iu.test(sql)));
});

test("normalized same-content save appends only an event and preserves policy time and completed work", async t => {
  const db = database(t);
  const before = current(db);
  const document = (await (await request(db)).json()).document;
  const response = await request(db, { method: "POST", body: saveBody("same-content-0001", `  ${document.content.replace(/\n/g, "\r\n")}  `) });
  const result = await response.json();
  assert.equal(result.save.changed, false);
  assert.equal(result.document.revision, 1);
  assert.deepEqual(current(db), before);
  assert.equal(result.application.pending_count, 0);
  assert.equal(count(db, "ai_practice_policy_snapshots"), 1);
  assert.equal(count(db, "ai_practice_manual_rule_events"), 1);
});

test("logical pending counts stale processed/review once and distinguishes first evaluation from reassessment", async t => {
  const db = database(t);
  db.sqlite.exec(`UPDATE ai_practice_queue SET status='review' WHERE item_id='item-0';
    UPDATE ai_practice_candidates SET instruction_version='ai-practice-v6' WHERE item_id='item-1';
    UPDATE ai_practice_queue SET processed_instruction_version='ai-practice-v6' WHERE item_id='item-2';
    INSERT INTO ai_practice_queue(item_id,source_key,canonical_url,descriptor_json,status,work_kind,discovered_at,updated_at)
      VALUES('new-discovery','ai-practice-hermes','https://example.com/new','{}','pending','recent','2026-11-07','2026-11-07');`);
  const before = (await (await request(db)).json()).application;
  assert.equal(before.total_count, 4);
  assert.equal(before.pending_count, 3);
  assert.equal(before.stale_count, 3);
  assert.equal(before.reassessment_count, 2, "newly registered URL has no previous evaluation to reassess");
  assert.equal(before.review_count, 1, "current-policy review is separate from processing backlog");
  const saved = await (await request(db, { method: "POST", body: saveBody() })).json();
  assert.equal(saved.application.pending_count, 4, "old-policy review joins backlog without a queue UPDATE");
  assert.equal(saved.application.reassessment_count, 3);
  assert.equal(saved.application.stale_count, 4);
});

test("validity version, evidence and time bounds invalidate completed work without changing its policy or body", async t => {
  const now = Date.now();
  const cases = [
    ["expired", "validity_expires_at", new Date(now - 1).toISOString()],
    ["future check", "validity_checked_at", new Date(now + 3600_000).toISOString()],
    ["missing check", "validity_checked_at", null],
    ["invalid check", "validity_checked_at", "invalid"],
    ["missing expiry", "validity_expires_at", null],
    ["invalid expiry", "validity_expires_at", "invalid"],
    ["missing evidence", "validity_json", null],
    ["old manifest", "validity_version", "ai-validity-old"],
    ["old instruction", "instruction_version", "ai-practice-v8"],
  ];
  for (const [name, column, value] of cases) await t.test(name, async sub => {
    const db = database(sub);
    const fresh = (await (await request(db)).json()).application;
    assert.equal(fresh.pending_count, 0);
    db.sqlite.prepare(`UPDATE ai_practice_candidates SET ${column}=? WHERE item_id='item-0'`).run(value);
    const before = db.sqlite.prepare("SELECT * FROM ai_practice_queue ORDER BY item_id").all();
    const writes = db.sqlite.prepare("SELECT total_changes() AS n").get().n;
    const response = await request(db);
    const result = await response.json();
    assert.equal(response.status, 200);
    assert.equal(result.document.revision, 1);
    assert.equal(result.application.total_count, 3);
    assert.equal(result.application.pending_count, 1);
    assert.equal(result.application.stale_count, 1);
    assert.equal(result.application.reassessment_count, 1);
    assert.equal(result.application.sources.find(row => row.source_key === "ai-practice-hermes").stale_count, 1);
    assert.deepEqual(db.sqlite.prepare("SELECT * FROM ai_practice_queue ORDER BY item_id").all(), before);
    assert.equal(db.sqlite.prepare("SELECT total_changes() AS n").get().n, writes);
    assert.doesNotMatch(JSON.stringify(result), /validity_json|reusable design principle/u);
  });
});

test("validity bounds compare actual instants and a stale review joins reassessment only once", async t => {
  const db = database(t);
  const validity = validityFixture();
  const offsetTime = value => `${new Date(Date.parse(value) + 9 * 3600_000).toISOString().slice(0, -1)}+09:00`;
  db.sqlite.prepare("UPDATE ai_practice_candidates SET validity_checked_at=?,validity_expires_at=? WHERE item_id='item-0'")
    .run(offsetTime(validity.checked_at), offsetTime(validity.expires_at));
  assert.equal((await (await request(db)).json()).application.pending_count, 0);
  db.sqlite.exec("UPDATE ai_practice_queue SET status='review' WHERE item_id='item-0'");
  db.sqlite.prepare("UPDATE ai_practice_candidates SET validity_expires_at=? WHERE item_id='item-0'").run(new Date().toISOString());
  const result = (await (await request(db)).json()).application;
  assert.equal(result.pending_count, 1);
  assert.equal(result.stale_count, 1);
  assert.equal(result.reassessment_count, 1);
  assert.equal(result.review_count, 1);
});

test("saving policy stays constant-write with a thousand completed candidates", async t => {
  const db = database(t);
  db.sqlite.exec(`WITH RECURSIVE ids(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM ids WHERE n<1000)
    INSERT INTO ai_practice_queue(item_id,source_key,canonical_url,descriptor_json,status,work_kind,discovered_at,updated_at,
      processed_policy_revision,processed_policy_hash,processed_instruction_version)
    SELECT 'extra-'||n,'ai-practice-hermes','https://example.com/extra-'||n,'{}','processed','backfill','2026-11-07','2026-11-07',
      1,(SELECT policy_hash FROM ai_practice_policy_state WHERE id=1),'ai-practice-v9' FROM ids;`);
  const writes = db.sqlite.prepare("SELECT total_changes() AS n").get().n;
  const saved = await (await request(db, { method: "POST", body: saveBody() })).json();
  assert.equal(saved.application.total_count, 1003);
  assert.equal(saved.application.pending_count, 1003);
  assert.equal(saved.application.reassessment_count, 1003);
  assert.equal(db.sqlite.prepare("SELECT total_changes() AS n").get().n - writes, 3);
  assert.equal(db.sqlite.prepare("SELECT count(*) AS n FROM ai_practice_queue WHERE status='processed'").get().n, 1003);
});

test("unregistered discovery manifests are reported separately and may contain duplicate URLs", async t => {
  const db = database(t);
  const insert = db.sqlite.prepare(`INSERT INTO ai_practice_discovery_manifests
    (manifest_id,source_key,lane,descriptor_json,item_count,registration_offset,status,discovered_at,updated_at,last_cycle_at)
    VALUES(?,?,?,?,?,?,?,?,?,'2026-11-07')`);
  const repeated = JSON.stringify(Array.from({length:500}, (_, index) => ({canonical_url:`https://example.com/${index}`})));
  insert.run("manifest-recent", "ai-practice-hermes", "recent", repeated, 500, 200, "pending", "2026-11-07", "2026-11-07");
  insert.run("manifest-backfill", "ai-practice-hermes", "backfill", repeated, 500, 100, "pending", "2026-11-07", "2026-11-07");
  insert.run("manifest-complete", "ai-practice-hermes", "backfill", repeated, 500, 500, "complete", "2026-11-07", "2026-11-07");
  insert.run("manifest-pal", "ai-practice-pal", "backfill", JSON.stringify([{canonical_url:"https://example.com/pal/1"},{canonical_url:"https://example.com/pal/2"}]), 2, 0, "pending", "2026-11-07", "2026-11-07");
  const application = (await (await request(db)).json()).application;
  assert.equal(application.total_count, 3, "only distinct registered URLs count as queue candidates");
  assert.equal(application.manifest_pending_count, 702);
  assert.equal(application.sources.find(row => row.source_key === "ai-practice-hermes").manifest_pending_count, 700);
  assert.equal(application.sources.find(row => row.source_key === "ai-practice-pal").manifest_pending_count, 2);
  assert.equal(application.pending_count, 0, "unregistered manifest entries do not inflate the queue backlog");
});

test("replayed saves cannot requeue finished work and reused IDs reject different payloads", async t => {
  const db = database(t);
  assert.equal((await request(db, { method: "POST", body: saveBody() })).status, 201);
  db.sqlite.exec(`UPDATE ai_practice_queue SET status='processed',processed_policy_revision=2,
    processed_policy_hash=(SELECT policy_hash FROM ai_practice_policy_state WHERE id=1);
    UPDATE ai_practice_candidates SET policy_revision=2,policy_hash=(SELECT policy_hash FROM ai_practice_policy_state WHERE id=1);`);
  const replay = await request(db, { method: "POST", body: saveBody() });
  assert.equal(replay.status, 201);
  assert.equal((await replay.json()).save.replayed, true);
  assert.equal((await (await request(db)).json()).application.pending_count, 0);
  assert.equal(count(db, "ai_practice_manual_rule_events"), 1);
  assert.ok(db.sqlite.prepare("SELECT status FROM ai_practice_queue").all().every(x => x.status === "processed"));
  assert.equal((await request(db, { method: "POST", body: saveBody(undefined, "changed payload") })).status, 409);
});

test("a concurrent revision winner leaves no partial snapshot or event for the losing save", async t => {
  const db = database(t);
  db.beforeBatch = async () => {
    assert.equal((await request(db, { method: "POST", body: saveBody("winning-event-0001", "winning policy") })).status, 201);
  };
  const response = await request(db, { method: "POST", body: saveBody("losing-event-00001") });
  assert.equal(response.status, 409);
  assert.equal(count(db, "ai_practice_manual_rule_events"), 1);
  assert.equal(count(db, "ai_practice_policy_snapshots"), 2);
  assert.equal((await (await request(db)).json()).document.content, "winning policy");
});

test("a simultaneous idempotent retry does not reset work completed after the first response", async t => {
  const db = database(t);
  db.beforeBatch = async () => {
    assert.equal((await request(db, { method: "POST", body: saveBody() })).status, 201);
    db.sqlite.exec(`UPDATE ai_practice_queue SET status='processed',processed_policy_revision=2,
      processed_policy_hash=(SELECT policy_hash FROM ai_practice_policy_state WHERE id=1);
      UPDATE ai_practice_candidates SET policy_revision=2,policy_hash=(SELECT policy_hash FROM ai_practice_policy_state WHERE id=1);`);
  };
  const response = await request(db, { method: "POST", body: saveBody() });
  assert.equal(response.status, 201);
  assert.equal((await response.json()).save.replayed, true);
  assert.equal(count(db, "ai_practice_manual_rule_events"), 1);
  assert.ok(db.sqlite.prepare("SELECT status FROM ai_practice_queue").all().every(x => x.status === "processed"));
});

test("a failed transaction rolls back state, immutable snapshot and event while leaving queue untouched", async t => {
  const db = database(t);
  db.failAt = 2;
  const response = await request(db, { method: "POST", body: saveBody() });
  assert.equal(response.status, 500);
  assert.doesNotMatch(await response.text(), /PRIVATE-AI|internal database/);
  assert.equal(current(db).policy_revision, 1);
  assert.equal(count(db, "ai_practice_policy_snapshots"), 1);
  assert.equal(count(db, "ai_practice_manual_rule_events"), 0);
  assert.ok(db.sqlite.prepare("SELECT status FROM ai_practice_queue").all().every(x => x.status === "processed"));
});

test("writes enforce same origin, marker, length and optimistic version before mutation", async t => {
  const db = database(t);
  for (const headers of [{ Origin: "https://other.example" }, { "Sec-Fetch-Site": "cross-site" }, { "X-TodayCommunity-Write": "" }]) {
    assert.equal((await request(db, { method: "POST", body: saveBody(), headers })).status, 403);
  }
  for (const content of [" \r\n ", "가".repeat(6001), null]) {
    assert.equal((await request(db, { method: "POST", body: saveBody("length-test-00001", content) })).status, 400);
  }
  assert.equal((await request(db, { method: "POST", body: saveBody("stale-base-000001", privateText, 9) })).status, 409);
  assert.equal(count(db, "ai_practice_manual_rule_events"), 0);
  const boundary = await request(db, { method: "POST", body: saveBody("length-test-00002", "가".repeat(6000)) });
  assert.equal(boundary.status, 201);
});

test("missing policy initialization fails closed without embedding a fallback private document", async t => {
  const db = database(t);
  db.sqlite.exec("DELETE FROM ai_practice_policy_state;");
  const response = await request(db);
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("cache-control"), "private, no-store");
  assert.doesNotMatch(await response.text(), /content|PRIVATE-AI/);
});

test("Cloudflare D1 batch preserves changes() guards for policy writes and idempotent replay", async t => {
  const fixture = database(t);
  const runtime = new Miniflare(convertV4MiniflareOptions({
    modulesRoot: fileURLToPath(new URL("../", import.meta.url)),
    modules: [
      { type: "ESModule", path: "test-ai-admin-worker.mjs", contents: `import * as api from "./test-ai-admin-api.mjs";
        export default { fetch(request, env) { return api['onRequest' + request.method[0] + request.method.slice(1).toLowerCase()]({request, env}); } };` },
      { type: "ESModule", path: "test-ai-admin-api.mjs", contents: source.replace(JSON.stringify(authUrl), '"./test-ai-auth.mjs"') },
      { type: "ESModule", path: "test-ai-auth.mjs", contents: authSource },
    ],
    compatibilityDate: "2026-08-01", cf: false, telemetry: { enabled: false },
    bindings: { TC_AUTH_SESSION_SECRET: secret }, d1Databases: ["DB"],
  }));
  t.after(() => runtime.dispose());
  const db = await runtime.getD1Database("DB");
  const schema = fixture.sqlite.prepare("SELECT sql FROM sqlite_schema WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY rowid").all();
  await db.batch(schema.map(row => db.prepare(row.sql)));
  const inserts = [];
  for (const table of ["auth_secret_links", "sources", "posts", "ai_practice_candidates", "ai_practice_policy_snapshots", "ai_practice_policy_state", "ai_practice_queue", "ai_practice_discovery_state"]) {
    for (const row of fixture.sqlite.prepare(`SELECT * FROM ${table}`).all()) {
      const fields = Object.keys(row);
      inserts.push(db.prepare(`INSERT INTO ${table} (${fields.join(",")}) VALUES (${fields.map(() => "?").join(",")})`).bind(...Object.values(row)));
    }
  }
  await db.batch(inserts);
  const send = body => runtime.dispatchFetch("https://todaycommunity.example/api/ai-practice/rules", {
    method: "POST", headers: { Cookie: `__Host-tc_admin=${admin}`, Origin: "https://todaycommunity.example",
      "Content-Type": "application/json", "X-TodayCommunity-Write": "1", "Sec-Fetch-Site": "same-origin" }, body: JSON.stringify(body),
  });
  const saved = await send(saveBody());
  assert.equal(saved.status, 201);
  const first = await saved.json();
  assert.equal(first.document.revision, 2);
  assert.equal(first.application.reassessment_count, 3);
  await db.batch([
    db.prepare(`UPDATE ai_practice_queue SET status='processed',processed_policy_revision=2,
      processed_policy_hash=(SELECT policy_hash FROM ai_practice_policy_state WHERE id=1)`),
    db.prepare(`UPDATE ai_practice_candidates SET policy_revision=2,policy_hash=(SELECT policy_hash FROM ai_practice_policy_state WHERE id=1)`),
  ]);
  const replayed = await send(saveBody());
  const second = await replayed.json();
  assert.equal(replayed.status, 201);
  assert.equal(second.save.replayed, true);
  assert.equal(second.application.pending_count, 0);
  const unchanged = await send(saveBody("runtime-same-0001", privateText, 2));
  const third = await unchanged.json();
  assert.equal(unchanged.status, 201);
  assert.equal(third.save.changed, false);
  assert.equal(third.application.pending_count, 0);
  const criteria = await runtime.dispatchFetch("https://todaycommunity.example/api/ai-practice/criteria", {
    headers: { Cookie: `__Host-tc_authenticated=${link}` },
  });
  assert.equal(criteria.status, 200);
  assert.deepEqual(await criteria.json(), { document: { content: privateText, revision: 2, updated_at: third.document.updated_at } });
  const readOnly = await runtime.dispatchFetch("https://todaycommunity.example/api/ai-practice/criteria", {
    method: "PUT", headers: { Cookie: `__Host-tc_authenticated=${link}` },
  });
  assert.equal(readOnly.status, 405);
  assert.equal(readOnly.headers.get("allow"), "GET");
  await db.batch([
    db.prepare("UPDATE ai_practice_candidates SET validity_expires_at=? WHERE item_id='item-0'").bind(new Date(Date.now() - 1).toISOString()),
    db.prepare("UPDATE ai_practice_candidates SET instruction_version='ai-practice-v8' WHERE item_id='item-1'"),
    db.prepare("UPDATE ai_practice_candidates SET validity_version='ai-validity-old' WHERE item_id='item-2'"),
  ]);
  const staleResponse = await runtime.dispatchFetch("https://todaycommunity.example/api/ai-practice/rules", {
    headers: { Cookie: `__Host-tc_admin=${admin}` },
  });
  assert.equal(staleResponse.status, 200);
  const stale = await staleResponse.json();
  assert.equal(stale.document.revision, 2);
  assert.equal(stale.application.stale_count, 3);
  assert.equal(stale.application.pending_count, 3);
  assert.equal(stale.application.reassessment_count, 3);
  assert.equal((await db.prepare("SELECT COUNT(*) AS n FROM ai_practice_queue WHERE status='processed'").first()).n, 3);
});
