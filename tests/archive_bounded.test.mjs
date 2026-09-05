import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";

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
