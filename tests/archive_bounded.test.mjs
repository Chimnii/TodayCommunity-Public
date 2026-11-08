import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { publicationClock, seedAiInclude } from "./helpers/ai-first-publication.mjs";

const source = await readFile(new URL("../functions/api/archive.js", import.meta.url), "utf8");
const { onRequestGet } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const schema = await readFile(new URL("../cloudflare/schema.sql", import.meta.url), "utf8");

function fixture(count = 1200) {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(schema);
  sqlite.exec("INSERT INTO sources(source_key,archive_key,site_name,board_name,board_url,min_upvotes,min_comments) VALUES('fixture','dcinside-singularity','fixture','fixture','https://example.com',0,0)");
  const insert = sqlite.prepare(`INSERT INTO posts (
    id, source_key, archive_key, external_post_id, canonical_post_key, title, post_url,
    created_at, created_at_raw, fetched_at, first_seen_at, last_seen_at, qualifies_by, upvotes, comments
  ) VALUES (?, 'fixture', 'dcinside-singularity', ?, ?, ?, 'https://example.com',
    '2026-09-05T00:00:00Z', '2026-09-05T00:00:00Z', '2026-09-05T00:00:00Z', '2026-09-05T00:00:00Z',
    '2026-09-05T00:00:00Z', 'fixture', ?, ?)`);
  sqlite.exec("BEGIN");
  for (let i = 1; i <= count; i += 1) insert.run(i, String(i), String(i), i % 97 === 0 ? "needle" : "post", i % 7, i % 5);
  sqlite.prepare("UPDATE archive_stats SET active_post_count=? WHERE archive_key='dcinside-singularity'").run(count);
  sqlite.exec("COMMIT");
  const queries = [];
  const prepare = (sql, values = []) => ({
    bind(...args) { return prepare(sql, args); },
    async all() {
      const results = sqlite.prepare(sql).all(...values).map((row) => ({ ...row }));
      queries.push({ sql, values, rows: results.length });
      // SQLite cannot measure D1 billed rows: deliberately omit those metrics.
      return { results };
    },
  });
  const db = { prepare, batch: (items) => Promise.all(items.map((item) => item.all())) };
  return { sqlite, db, queries };
}

async function request(data, params, headers = {}) {
  const response = await onRequestGet({ request: new Request(`https://example.com/api/archive?${params}`, { headers }), env: { DB: data.db } });
  const body = await response.json();
  assert.equal(response.status, 200, body.error);
  return { response, body };
}

test("retiring Zeus hides its preserved rows and metadata from direct and combined archives", async () => {
  const data = fixture(3);
  try {
    data.sqlite.exec(`UPDATE archives SET is_public=1 WHERE archive_key='dcinside-zeus-pride';
      INSERT INTO sources(source_key,archive_key,site_name,board_name,board_url)
        VALUES('dcinside-zeus-pride','dcinside-zeus-pride','dcinside','Zeus','https://example.com');
      UPDATE posts SET archive_key='dcinside-zeus-pride',source_key='dcinside-zeus-pride' WHERE id=3;
      UPDATE archive_stats SET active_post_count=2 WHERE archive_key='dcinside-singularity';
      UPDATE archive_stats SET active_post_count=1 WHERE archive_key='dcinside-zeus-pride'`);
    assert.equal((await request(data, "target=dcinside-zeus-pride")).body.posts.length, 1);
    const migration = await readFile(new URL("../cloudflare/migrations/019_retire_zeus_pride_archive.sql", import.meta.url), "utf8");
    data.sqlite.exec(migration);
    const retired = await onRequestGet({
      request: new Request("https://example.com/api/archive?target=dcinside-zeus-pride"),
      env: { DB: data.db },
    });
    assert.equal(retired.status, 400);
    assert.equal((await retired.json()).error, "Unknown archive target.");
    for (const params of ["target=all", "target=all&q=post", "target=dcinside-singularity"]) {
      const { body } = await request(data, params);
      assert.ok(body.posts.every(row => row.archive_key !== "dcinside-zeus-pride"));
      assert.ok(body.archives.every(row => row.archive_key !== "dcinside-zeus-pride"));
      assert.ok(body.sources.every(row => row.archive_key !== "dcinside-zeus-pride"));
      assert.equal(body.summary.total_posts, 2);
    }
    assert.equal(data.sqlite.prepare("SELECT count(*) AS n FROM posts").get().n, 3);
  } finally { data.sqlite.close(); }
});

test("real SQL traverses tied keys both ways without gaps or duplicates for every sort", async () => {
  const data = fixture();
  try {
    for (const sort of ["created_at", "upvotes", "comments"]) {
      const params = new URLSearchParams({ q: "post", sort, page_size: "30" });
      const expected = data.sqlite.prepare(`SELECT external_post_id FROM posts WHERE title='post' ORDER BY ${sort} DESC, ${sort === "created_at" ? "" : "created_at DESC,"} id DESC`).all().map((row) => row.external_post_id);
      let page = (await request(data, params)).body;
      const collected = [];
      for (let step = 0; step < 60; step += 1) {
        collected.push(...page.posts.map((row) => row.external_post_id));
        if (!page.pagination.has_next) break;
        const prior = page;
        params.set("cursor", page.pagination.next_cursor);
        page = (await request(data, params)).body;
        const back = new URLSearchParams(params);
        back.set("cursor", page.pagination.previous_cursor);
        const previous = (await request(data, back)).body;
        assert.deepEqual(previous.posts, prior.posts);
      }
      assert.deepEqual(collected, expected);
    }
    for (const query of data.queries.filter((item) => item.sql.includes("WITH candidates"))) {
      assert.ok(query.rows <= 512);
      assert.doesNotMatch(query.sql, /OFFSET/i);
    }
  } finally { data.sqlite.close(); }
});

test("empty sparse windows remain navigable and never report an exhausted search early", async () => {
  const data = fixture();
  try {
    data.sqlite.exec("UPDATE posts SET title=CASE WHEN id=1 THEN 'rare' ELSE 'post' END");
    const params = new URLSearchParams({ q: "rare" });
    let page = (await request(data, params)).body;
    assert.equal(page.posts.length, 0);
    assert.equal(page.pagination.has_next, true);
    params.set("cursor", page.pagination.next_cursor);
    page = (await request(data, params)).body;
    assert.equal(page.posts.length, 0);
    assert.equal(page.pagination.has_previous, true);
    assert.equal(page.pagination.has_next, true);
    const back = new URLSearchParams(params);
    back.set("cursor", page.pagination.previous_cursor);
    const previous = (await request(data, back)).body;
    assert.equal(previous.pagination.page, 1);
    assert.equal(previous.posts.length, 0);
    params.set("cursor", page.pagination.next_cursor);
    page = (await request(data, params)).body;
    assert.deepEqual(page.posts.map((row) => row.external_post_id), ["1"]);
    assert.equal(page.pagination.has_next, false);
  } finally { data.sqlite.close(); }
});

test("large archives offer first/last and bounded adjacent navigation, rejecting deep offset jumps", async () => {
  const data = fixture(5005);
  try {
    const first = (await request(data, new URLSearchParams())).body;
    assert.equal(first.pagination.mode, "sequential");
    const next = (await request(data, new URLSearchParams({ cursor: first.pagination.next_cursor }))).body;
    assert.equal(next.posts[0].external_post_id, "4975");
    const last = (await request(data, new URLSearchParams({ page: "999999" }))).body;
    assert.equal(last.pagination.page, 167);
    assert.equal(last.posts.length, 25);
    assert.equal(last.posts.at(-1).external_post_id, "1");
    assert.equal(last.pagination.has_next, false);
    const prior = (await request(data, new URLSearchParams({ cursor: last.pagination.previous_cursor }))).body;
    assert.deepEqual(prior.posts.map((row) => Number(row.external_post_id)), Array.from({ length: 30 }, (_, i) => 55 - i));
    const response = await onRequestGet({ request: new Request("https://example.com/api/archive?page=80"), env: { DB: data.db } });
    assert.equal(response.status, 400);
    assert.equal((await response.json()).code, "deep_page_requires_cursor");
    assert.ok(data.queries.filter((item) => item.sql.includes("WITH candidates")).every((item) => item.rows <= 31));
  } finally { data.sqlite.close(); }
});

test("an exactly exhausted empty window can navigate back using its original boundary", async () => {
  const data = fixture(1024);
  try {
    const params = new URLSearchParams({ q: "absent" });
    let page = (await request(data, params)).body;
    for (let i = 0; i < 2; i += 1) {
      params.set("cursor", page.pagination.next_cursor);
      page = (await request(data, params)).body;
    }
    assert.equal(page.pagination.page, 3);
    assert.equal(page.pagination.has_next, false);
    assert.equal(page.pagination.has_previous, true);
    assert.ok(page.pagination.previous_cursor);
    params.set("cursor", page.pagination.previous_cursor);
    const previous = (await request(data, params)).body;
    assert.equal(previous.pagination.page, 2);
    assert.equal(previous.pagination.has_previous, true);
    assert.equal(previous.pagination.has_next, true);
  } finally { data.sqlite.close(); }
});

test("changing page size invalidates the cursor before querying D1", async () => {
  const data = fixture();
  try {
    const first = (await request(data, new URLSearchParams({ q: "post", page_size: "30" }))).body;
    const count = data.queries.length;
    const params = new URLSearchParams({ q: "post", page_size: "100", cursor: first.pagination.next_cursor });
    const response = await onRequestGet({ request: new Request(`https://example.com/api/archive?${params}`), env: { DB: data.db } });
    assert.equal(response.status, 400);
    assert.equal(data.queries.length, count);
  } finally { data.sqlite.close(); }
});

test("small numbered last page reverses from the tail with a bounded offset", async () => {
  const data = fixture(95);
  try {
    const page = (await request(data, new URLSearchParams({ page: "4" }))).body;
    assert.equal(page.pagination.mode, "numbered");
    assert.deepEqual(page.posts.map((row) => Number(row.external_post_id)), [5, 4, 3, 2, 1]);
    const sql = data.queries.find((item) => item.sql.includes("id AS cursor_id"));
    assert.match(sql.sql, /ORDER BY created_at ASC, id ASC/);
    assert.deepEqual(sql.values.slice(-2), [5, 0]);
  } finally { data.sqlite.close(); }
});

test("all archive scans never expose private, hidden, or excluded candidates", async () => {
  const data = fixture(1200);
  try {
    data.sqlite.exec("UPDATE posts SET status='hidden' WHERE id%3=0; UPDATE archives SET is_public=0 WHERE archive_key='dcinside-agent-stack'; UPDATE posts SET archive_key='dcinside-agent-stack' WHERE id%3=1");
    const params = new URLSearchParams({ target: "all", page_size: "100" });
    let page = (await request(data, params)).body;
    const ids = [];
    for (let i = 0; i < 10; i += 1) {
      ids.push(...page.posts.map((row) => Number(row.external_post_id)));
      assert.ok(page.posts.every((row) => row.status === "active" && row.archive_key === "dcinside-singularity"));
      if (!page.pagination.has_next) break;
      params.set("cursor", page.pagination.next_cursor);
      page = (await request(data, params)).body;
    }
    assert.deepEqual(ids, Array.from({ length: 400 }, (_, i) => 1199 - i * 3));
    const excluded = (await request(data, new URLSearchParams({ target: "all", exclude_archive: "dcinside-singularity" }))).body;
    assert.equal(excluded.posts.length, 0);
    assert.equal(excluded.pagination.has_next, false);
  } finally { data.sqlite.close(); }
});

test("archive privacy changes after metadata lookup cannot expose posts or cursor boundaries", async (t) => {
  for (const [name, count, params] of [
    ["all filtered", 600, { target: "all", q: "post" }],
    ["all unfiltered", 600, { target: "all" }],
    ["single filtered", 600, { q: "post" }],
    ["single numbered", 600, {}],
    ["single large", 5001, {}],
  ]) {
    await t.test(name, async () => {
      const data = fixture(count);
      const originalPrepare = data.db.prepare;
      data.db.prepare = (sql) => {
        const statement = originalPrepare(sql);
        if (!sql.includes("COALESCE(stats.stats_version")) return statement;
        return {
          ...statement,
          async all() {
            const result = await statement.all();
            data.sqlite.exec("UPDATE archives SET is_public=0 WHERE archive_key='dcinside-singularity'");
            return result;
          },
        };
      };
      try {
        const page = (await request(data, new URLSearchParams(params))).body;
        assert.equal(page.posts.length, 0);
        assert.equal(page.pagination.previous_cursor, null);
        assert.equal(page.pagination.next_cursor, null);
        if (page.pagination.mode === "sequential") {
          assert.equal(page.pagination.has_next, false);
        }
      } finally { data.sqlite.close(); }
    });
  }
});

test("LIKE byte limits include escaping and return a clear input error before D1", async () => {
  for (const [text, valid] of [["가".repeat(16), true], ["가".repeat(17), false], ["😀".repeat(12), true], ["😀".repeat(13), false], ["%".repeat(24), true], ["%".repeat(25), false], ["_".repeat(25), false], ["\\".repeat(25), false], ["x".repeat(49), false]]) {
    const data = fixture(1);
    try {
      const params = new URLSearchParams({ q: text });
      const response = await onRequestGet({ request: new Request(`https://example.com/api/archive?${params}`), env: { DB: data.db } });
      assert.equal(response.status, valid ? 200 : 400, text);
      if (!valid) {
        assert.equal((await response.json()).code, "search_too_long");
        assert.equal(data.queries.length, 0);
      }
    } finally { data.sqlite.close(); }
  }
});

test("public owner responses share cache; refresh bypasses cached data without logging secrets", async () => {
  const data = fixture(5);
  const originalCaches = globalThis.caches;
  const originalLog = console.log;
  const logs = [];
  const cache = new Map();
  globalThis.caches = { default: {
    async match(key) { return cache.get(key.url)?.clone(); },
    async put(key, value) { cache.set(key.url, value.clone()); },
  } };
  console.log = (value) => logs.push(JSON.parse(value));
  try {
    await request(data, "");
    const count = data.queries.length;
    const owner = await request(data, "", { cookie: "__Host-tc_authenticated=secret-value" });
    assert.equal(owner.response.headers.get("x-tc-cache"), "hit");
    assert.equal(data.queries.length, count);
    data.sqlite.exec("UPDATE posts SET status='hidden' WHERE id=5");
    const refreshed = await request(data, "", { "x-tc-refresh": "1", cookie: "__Host-tc_authenticated=secret-value" });
    assert.equal(refreshed.response.headers.get("cache-control"), "no-store");
    assert.equal(refreshed.body.posts[0].external_post_id, "4");
    assert.ok(data.queries.length > count);
    assert.equal(JSON.stringify(logs).includes("secret-value"), false);
    assert.deepEqual(logs.map((item) => item.d1_api_usage.cache), ["miss", "hit", "bypass"]);
    assert.ok(logs[0].d1_api_usage.incomplete_meta > 0);
  } finally { globalThis.caches = originalCaches; console.log = originalLog; data.sqlite.close(); }
});


test("quick pages 1-5 match exact order and continue to page 6 with a reversible cursor", async () => {
  const data = fixture(6000);
  try {
    for (const target of ["dcinside-singularity", "all"]) {
      for (const sort of ["created_at", "upvotes", "comments"]) {
        const expected = data.sqlite.prepare(`SELECT external_post_id FROM posts ORDER BY ${sort} DESC, ${sort === "created_at" ? "" : "created_at DESC,"} id DESC`).all().map(row => row.external_post_id);
        for (const pageSize of [30, 100]) {
          let fifth;
          for (let page = 1; page <= 5; page += 1) {
            const params = new URLSearchParams({ target, sort, page: String(page), page_size: String(pageSize) });
            const body = (await request(data, params)).body;
            assert.equal(body.pagination.page, page);
            assert.equal(body.pagination.quick_page_count, 5);
            assert.deepEqual(body.posts.map(row => row.external_post_id), expected.slice((page - 1) * pageSize, page * pageSize));
            if (page === 5) fifth = body;
          }
          const sixth = (await request(data, new URLSearchParams({ target, sort, page_size: String(pageSize), cursor: fifth.pagination.next_cursor }))).body;
          assert.equal(sixth.pagination.page, 6);
          assert.deepEqual(sixth.posts.map(row => row.external_post_id), expected.slice(pageSize * 5, pageSize * 6));
          const back = (await request(data, new URLSearchParams({ target, sort, page_size: String(pageSize), cursor: sixth.pagination.previous_cursor }))).body;
          assert.deepEqual(back.posts, fifth.posts);
          const response = await onRequestGet({ request: new Request(`https://example.com/api/archive?target=${target}&page=6&page_size=${pageSize}`), env: { DB: data.db } });
          assert.equal(response.status, 400);
        }
      }
    }
    for (const query of data.queries.filter(item => item.sql.includes("WITH candidates"))) assert.ok(query.rows <= 501);
  } finally { data.sqlite.close(); }
});

test("metadata is reused across pages and sorts, expires without extending response freshness, and bypasses after a mutation", async () => {
  const data = fixture(6000);
  const stored = new Map();
  const original = globalThis.caches;
  globalThis.caches = { default: {
    async match(request) { return stored.get(request.url)?.clone(); },
    async put(request, response) { stored.set(request.url, response.clone()); },
  } };
  try {
    const first = (await request(data, new URLSearchParams({ page: "1" }))).body;
    const before = data.queries.length;
    const second = (await request(data, new URLSearchParams({ page: "2", sort: "upvotes" }))).body;
    assert.deepEqual(second.sources, first.sources);
    assert.deepEqual(second.runs, first.runs);
    assert.deepEqual(second.topic_trends, first.topic_trends);
    assert.equal(data.queries.length - before, 2, "only current stats/visibility and posts need D1");
    const unknownTopic = await onRequestGet({
      request: new Request("https://example.com/api/archive?topic=999999"), env: { DB: data.db },
    });
    assert.equal(unknownTopic.status, 400, "cached metadata cannot bypass selected topic validation");
    assert.equal((await unknownTopic.json()).error, "Unknown topic filter.");
    const metadataKey = [...stored.keys()].find(key => new URL(key).pathname === "/api/archive-metadata");
    const metadata = await stored.get(metadataKey).clone().json();
    stored.set(metadataKey, new Response(JSON.stringify({ ...metadata, expires_at: Date.now() + 9000 })));
    const third = await request(data, new URLSearchParams({ page: "3" }));
    assert.match(third.response.headers.get("cache-control"), /s-maxage=[0-9]$/);
    stored.set(metadataKey, new Response(JSON.stringify({ ...metadata, expires_at: Date.now() - 1 })));
    const beforeExpired = data.queries.length;
    await request(data, new URLSearchParams({ page: "4" }));
    assert.ok(data.queries.length - beforeExpired > 2);
    const beforeBypass = data.queries.length;
    const bypass = await request(data, new URLSearchParams({ page: "5" }), { "x-tc-refresh": "1" });
    assert.equal(bypass.response.headers.get("cache-control"), "no-store");
    assert.ok(data.queries.length - beforeBypass > 2);
    data.sqlite.exec("UPDATE archives SET is_public=0 WHERE archive_key='dcinside-agent-stack'");
    const beforeVisibility = data.queries.length;
    await request(data, new URLSearchParams({ page: "2", sort: "comments" }));
    assert.ok(data.queries.length - beforeVisibility > 2, "changed public catalog invalidates metadata");
  } finally { globalThis.caches = original; data.sqlite.close(); }
});

test("optional cache failures still return the bounded DB result", async () => {
  const data = fixture();
  const original = globalThis.caches;
  globalThis.caches = { default: {
    match() { throw new Error("synthetic cache failure"); },
    put() { throw new Error("synthetic cache failure"); },
  } };
  try {
    const { body } = await request(data, new URLSearchParams({ page: "5" }));
    assert.equal(body.posts.length, 30);
    assert.equal(body.pagination.page, 5);
  } finally { globalThis.caches = original; data.sqlite.close(); }
});

test("AI Korean and original titles share bounded search and subject/public visibility rules", async () => {
  const data = fixture(3);
  const clock = publicationClock(data.sqlite);
  clock.set("2026-11-09T01:00:00.000Z");
  try {
    const key = seedAiInclude(data.sqlite, { itemId: "ai-search-3", evaluatedAt: "2026-11-09T00:00:00Z" });
    data.sqlite.prepare("UPDATE posts SET canonical_post_key=? WHERE id=3").run(key);
    data.sqlite.exec(`INSERT OR IGNORE INTO archives(archive_key,display_name,content_kind) VALUES('ai-practice','AI 실전','article');
      INSERT OR IGNORE INTO archive_stats(archive_key) VALUES('ai-practice');
      UPDATE posts SET archive_key='ai-practice', title='프로젝트 운영 실패와 복구', original_title='Hermes operational recovery',
        summary='프로젝트 작업을 나누고 실패를 복구한 경험.', publisher='Hermes', subject='프로젝트 운영',
        content_kind='experience', evidence_level='full_text', created_at_basis='first_seen' WHERE id=3;
      UPDATE archive_stats SET active_post_count=1,subject_options_json='["프로젝트 운영"]' WHERE archive_key='ai-practice';
      UPDATE archive_stats SET active_post_count=2 WHERE archive_key='dcinside-singularity';`);
    for (const q of ['복구', 'Hermes']) {
      const { body } = await request(data, new URLSearchParams({target:'ai-practice',q,subject:'프로젝트 운영'}));
      assert.deepEqual(body.posts.map(row => row.post_id), [3]);
      assert.equal(body.posts[0].original_title, 'Hermes operational recovery');
      assert.equal(body.posts[0].summary, '프로젝트 작업을 나누고 실패를 복구한 경험.');
      assert.equal(body.posts[0].created_at_basis, 'first_seen');
      assert.equal(body.posts[0].content_kind, 'experience');
      assert.equal(body.posts[0].first_published_at, '2026-11-09T01:00:00.000Z');
    }
    assert.equal((await request(data, 'target=ai-practice&q=Hermes&subject=생활+비서')).body.posts.length, 0);
    assert.equal((await request(data, 'target=all&q=Hermes')).body.posts.length, 1);
    data.sqlite.exec("UPDATE archives SET is_public=0 WHERE archive_key='ai-practice'");
    assert.equal((await request(data, 'target=all&q=Hermes')).body.posts.length, 0);
    assert.ok(data.queries.filter(q => q.sql.includes('WITH candidates')).every(q => q.rows <= 512));
  } finally { data.sqlite.close(); clock.close(); }
});

function aiFixture(count = 1200) {
  const data = fixture(0);
  const clock = publicationClock(data.sqlite);
  const insert = data.sqlite.prepare(`INSERT INTO posts (
    id,source_key,archive_key,external_post_id,canonical_post_key,title,post_url,created_at,
    created_at_raw,created_at_basis,fetched_at,first_seen_at,last_seen_at,qualifies_by,subject,upvotes,comments)
    VALUES (?,'ai-practice-hermes','ai-practice',?,?,?,'https://example.com','2026-09-05T00:00:00Z',
      '2026-09-05T00:00:00Z','source','2026-09-05T00:00:00Z','2026-09-05T00:00:00Z',
      '2026-09-05T00:00:00Z','fixture','프로젝트 운영',?,?)`);
  data.sqlite.exec("BEGIN");
  for (let id = 1; id <= count; id++) {
    const timestamp = new Date(Date.UTC(2026, 10, 1) + ((id * 13) % 23) * 3600_000).toISOString();
    clock.set(timestamp);
    const key = seedAiInclude(data.sqlite, { itemId: `ai-fixture-${id}`, evaluatedAt: "2026-10-31T00:00:00Z" });
    insert.run(id, String(id), key, id % 97 === 0 ? "needle" : "post", id % 7, id % 5);
    assert.equal(data.sqlite.prepare("SELECT first_published_at FROM posts WHERE id=?").get(id).first_published_at, timestamp);
  }
  data.sqlite.prepare("UPDATE archive_stats SET active_post_count=? WHERE archive_key='ai-practice'").run(count);
  data.sqlite.exec("UPDATE archive_stats SET active_post_count=0 WHERE archive_key='dcinside-singularity'; COMMIT");
  return { ...data, clock };
}

test("AI first-publication SQL traverses tied dates both ways with the scoped index and original dates intact", async () => {
  const data = aiFixture();
  try {
    const expected = data.sqlite.prepare("SELECT id FROM posts WHERE title='post' ORDER BY first_published_at DESC,id DESC").all().map(row => row.id);
    const params = new URLSearchParams({ target: "ai-practice", q: "post", page_size: "30" });
    let page = (await request(data, params)).body;
    const collected = [];
    for (let step = 0; step < 60; step++) {
      collected.push(...page.posts.map(row => row.post_id));
      assert.ok(page.posts.every(row => row.first_published_at && row.created_at === "2026-09-05T00:00:00Z"));
      if (!page.pagination.has_next) break;
      const prior = page;
      const next = JSON.parse(Buffer.from(page.pagination.next_cursor, "base64url"));
      assert.equal(next.sort, "first_published_at");
      assert.equal(next.key.length, 2);
      assert.equal(JSON.parse(next.signature).ordering, "ai-first-published-v1");
      params.set("cursor", page.pagination.next_cursor);
      page = (await request(data, params)).body;
      const back = new URLSearchParams(params);
      back.set("cursor", page.pagination.previous_cursor);
      assert.deepEqual((await request(data, back)).body.posts, prior.posts);
    }
    assert.deepEqual(collected, expected);
    assert.equal(new Set(collected).size, collected.length);
    for (const query of data.queries.filter(row => row.sql.includes("id AS cursor_id"))) {
      const plan = data.sqlite.prepare(`EXPLAIN QUERY PLAN ${query.sql}`).all(...query.values).map(row => row.detail).join("\n");
      assert.match(plan, /USING INDEX idx_posts_ai_first_published/, plan);
      // The final "SCAN posts" reads the already capped MATERIALIZED candidates
      // alias; the underlying posts lookup must seek the dedicated index.
      assert.match(plan, /SEARCH posts USING INDEX idx_posts_ai_first_published/, plan);
      assert.doesNotMatch(plan, /USE TEMP B-TREE|USING INDEX idx_posts_archive_created_at/, plan);
      assert.ok(query.rows <= 512);
    }
  } finally { data.sqlite.close(); data.clock.close(); }
});

test("AI numbered and large last-page navigation use first-publication dates instead of source dates", async () => {
  for (const count of [95, 1200]) {
    const data = aiFixture(count);
    try {
      const expected = data.sqlite.prepare("SELECT id FROM posts ORDER BY first_published_at DESC,id DESC").all().map(row => row.id);
      const first = (await request(data, "target=ai-practice")).body;
      assert.deepEqual(first.posts.map(row => row.post_id), expected.slice(0, 30));
      const last = (await request(data, "target=ai-practice&page=9999")).body;
      const lastOffset = (Math.ceil(count / 30) - 1) * 30;
      assert.deepEqual(last.posts.map(row => row.post_id), expected.slice(lastOffset));
      assert.equal(last.pagination.has_next, false);
      if (count > 150) {
        const fifth = (await request(data, "target=ai-practice&page=5")).body;
        assert.deepEqual(fifth.posts.map(row => row.post_id), expected.slice(120, 150));
        const sixth = (await request(data, new URLSearchParams({ target: "ai-practice", cursor: fifth.pagination.next_cursor }))).body;
        assert.deepEqual(sixth.posts.map(row => row.post_id), expected.slice(150, 180));
        const back = (await request(data, new URLSearchParams({ target: "ai-practice", cursor: sixth.pagination.previous_cursor }))).body;
        assert.deepEqual(back.posts, fifth.posts);
      }
      for (const query of data.queries.filter(row => row.sql.includes("id AS cursor_id"))) {
        const plan = data.sqlite.prepare(`EXPLAIN QUERY PLAN ${query.sql}`).all(...query.values).map(row => row.detail).join("\n");
        assert.match(plan, /USING INDEX idx_posts_ai_first_published/, plan);
      }
    } finally { data.sqlite.close(); data.clock.close(); }
  }
});

test("AI empty filtered windows and exhausted cursor boundaries remain reversible", async () => {
  const data = aiFixture();
  try {
    const lastId = data.sqlite.prepare("SELECT id FROM posts ORDER BY first_published_at,id LIMIT 1").get().id;
    data.sqlite.prepare("UPDATE posts SET title=CASE WHEN id=? THEN 'rare' ELSE 'other' END").run(lastId);
    const params = new URLSearchParams({ target: "ai-practice", q: "rare" });
    let page = (await request(data, params)).body;
    assert.equal(page.posts.length, 0);
    assert.equal(page.pagination.has_next, true);
    const found = [];
    for (let step = 0; step < 5; step++) {
      found.push(...page.posts.map(row => row.post_id));
      if (!page.pagination.has_next) break;
      params.set("cursor", page.pagination.next_cursor);
      page = (await request(data, params)).body;
    }
    assert.deepEqual(found, [lastId]);
    const first = (await request(data, "target=ai-practice")).body;
    data.sqlite.exec("UPDATE posts SET status='hidden'");
    const empty = (await request(data, new URLSearchParams({ target: "ai-practice", cursor: first.pagination.next_cursor }))).body;
    assert.equal(empty.posts.length, 0);
    assert.equal(empty.pagination.has_previous, true);
    const boundary = JSON.parse(Buffer.from(empty.pagination.previous_cursor, "base64url"));
    assert.equal(boundary.key.length, 2);
    assert.match(boundary.key[0], /^2026-11-01T/);
    data.sqlite.exec("UPDATE posts SET status='active'");
    const previous = (await request(data, new URLSearchParams({ target: "ai-practice", cursor: empty.pagination.previous_cursor }))).body;
    assert.deepEqual(previous.posts, first.posts);
  } finally { data.sqlite.close(); data.clock.close(); }
});

test("AI ordering separates old cursors and edge cache while other archives reject the AI-only mode", async () => {
  const data = aiFixture(95);
  const original = globalThis.caches;
  const keys = [];
  globalThis.caches = { default: { async match(key) { keys.push(key.url); }, async put() {} } };
  try {
    const first = (await request(data, "target=ai-practice&q=post")).body;
    const decoded = JSON.parse(Buffer.from(first.pagination.next_cursor, "base64url"));
    const signature = JSON.parse(decoded.signature);
    delete signature.ordering;
    decoded.signature = JSON.stringify(signature);
    const stale = Buffer.from(JSON.stringify(decoded)).toString("base64url");
    const response = await onRequestGet({ request: new Request(`https://example.com/api/archive?target=ai-practice&q=post&cursor=${stale}`), env: { DB: data.db } });
    assert.equal(response.status, 400);
    for (const target of ["game-news", "dcinside-singularity", "all", "favorites"]) {
      const rejected = await onRequestGet({ request: new Request(`https://example.com/api/archive?target=${target}&sort=first_published_at`), env: { DB: data.db } });
      assert.equal(rejected.status, 400);
    }
    const responseKey = keys.find(key => new URL(key).pathname === "/api/archive");
    assert.equal(new URL(responseKey).searchParams.get("ordering"), "ai-first-published-v1");
    assert.equal(new URL(responseKey).searchParams.get("sort"), "first_published_at");
    await request(data, "target=game-news");
    const gameKey = keys.find(key => new URL(key).pathname === "/api/archive" && new URL(key).searchParams.get("target") === "game-news");
    assert.equal(new URL(gameKey).searchParams.has("ordering"), false);
    assert.equal(new URL(gameKey).searchParams.get("sort"), "created_at");
  } finally { globalThis.caches = original; data.sqlite.close(); data.clock.close(); }
});
