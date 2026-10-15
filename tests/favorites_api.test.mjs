import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { fixture, request } from "./helpers/favorites-fixture.mjs";

test("only active secret-link sessions may read or write shared favorites", async () => {
  const data = await fixture();
  try {
    for (const who of ["guest", "admin"]) {
      for (const options of [{}, { query: "ids=1" }, { body: { post_id: 1, saved: true } }]) {
        const result = await request(data, { who, ...options });
        assert.equal(result.status, 401);
        assert.match(result.headers.get("Cache-Control"), /private, no-store/);
      }
    }
    data.sqlite.exec("UPDATE auth_secret_links SET revoked_at='2026-01-02' WHERE id=1");
    const revoked = await request(data);
    assert.equal(revoked.status, 401);
    assert.match(revoked.headers.get("Set-Cookie"), /Max-Age=0/);
    data.sqlite.exec("UPDATE auth_secret_links SET expires_at='2026-01-02' WHERE id=2");
    assert.equal((await request(data, { who: "secret2" })).status, 401);
    assert.equal(data.sqlite.prepare("SELECT count(*) AS n FROM shared_favorites").get().n, 0);
  } finally { data.sqlite.close(); }
});

test("two independent secrets share saves, removals and idempotent ordering", async () => {
  const data = await fixture();
  try {
    for (const post_id of [1, 2]) assert.equal((await request(data, { body: { post_id, saved: true } })).status, 200);
    const before = data.sqlite.prepare("SELECT * FROM shared_favorites ORDER BY id").all();
    await request(data, { who: "secret2", body: { post_id: 1, saved: true } });
    assert.deepEqual(data.sqlite.prepare("SELECT * FROM shared_favorites ORDER BY id").all(), before);
    assert.equal(data.sqlite.prepare("SELECT seq FROM sqlite_sequence WHERE name='shared_favorites'").get().seq, 2);
    assert.deepEqual((await request(data, { who: "secret2", query: "ids=1,2,3" })).body.post_ids, [1, 2]);
    const list = await request(data, { who: "secret2" });
    assert.deepEqual(list.body.posts.map(p => p.post_id), [2, 1]);
    assert.equal(list.body.summary.total_posts, 2);
    assert.equal(list.headers.get("Vary"), "Cookie");
    await request(data, { who: "secret2", body: { post_id: 1, saved: false } });
    await request(data, { body: { post_id: 1, saved: false } });
    assert.deepEqual((await request(data, { query: "ids=1,2" })).body.post_ids, [2]);
    await request(data, { body: { post_id: 1, saved: true } });
    assert.deepEqual((await request(data)).body.posts.map(p => p.post_id), [1, 2]);
  } finally { data.sqlite.close(); }
});

test("favorites paginate in both directions without duplication and retain invisible saves", async () => {
  const data = await fixture();
  try {
    data.sqlite.exec("INSERT INTO shared_favorites(post_id) SELECT id FROM posts ORDER BY id");
    const first = (await request(data)).body;
    assert.equal(first.posts.length, 30);
    const second = (await request(data, { query: `cursor=${first.pagination.next_cursor}` })).body;
    const third = (await request(data, { query: `cursor=${second.pagination.next_cursor}` })).body;
    assert.equal(third.posts.length, 5);
    assert.equal(third.pagination.has_next, false);
    assert.deepEqual([...first.posts, ...second.posts, ...third.posts].map(p => p.post_id), Array.from({ length: 65 }, (_, i) => 65 - i));
    const back = (await request(data, { query: `cursor=${third.pagination.previous_cursor}` })).body;
    assert.deepEqual(back.posts, second.posts);
    assert.deepEqual((await request(data, { query: `cursor=${back.pagination.previous_cursor}` })).body.posts, first.posts);
    data.sqlite.exec("UPDATE posts SET status='hidden' WHERE id=65; UPDATE archives SET is_public=0 WHERE archive_key='game-news'");
    assert.equal((await request(data, { query: "ids=64,65" })).body.post_ids.length, 0);
    const filtered = (await request(data)).body;
    assert.equal(filtered.summary.total_posts, 32);
    assert.ok(filtered.posts.every(p => p.post_id % 2 && p.post_id !== 65));
    assert.equal((await request(data, { body: { post_id: 64, saved: true } })).status, 404);
    assert.equal((await request(data, { body: { post_id: 65, saved: true } })).status, 404);
    data.sqlite.exec("UPDATE posts SET status='active' WHERE id=65; UPDATE archives SET is_public=1 WHERE archive_key='game-news'");
    assert.deepEqual((await request(data)).body.posts, first.posts);
    data.sqlite.exec("DELETE FROM posts WHERE id=65");
    assert.equal(data.sqlite.prepare("SELECT count(*) AS n FROM shared_favorites WHERE post_id=65").get().n, 0);
  } finally { data.sqlite.close(); }
});

test("invisible candidates are bounded and empty pages retain continuation", async () => {
  const data = await fixture(600);
  try {
    data.sqlite.exec("INSERT INTO shared_favorites(post_id) SELECT id FROM posts ORDER BY id; UPDATE posts SET status='hidden' WHERE id>80");
    const first = (await request(data)).body;
    assert.equal(first.posts.length, 0);
    assert.equal(first.pagination.has_next, true);
    const next = (await request(data, { query: `cursor=${first.pagination.next_cursor}` })).body;
    assert.equal(next.posts.length, 30);
    assert.equal(next.posts[0].post_id, 80);
    assert.ok(data.queries.filter(q => q.sql.includes("WITH candidates")).every(q => q.count <= 512));
  } finally { data.sqlite.close(); }
});

test("write protection and input limits reject malformed or cross-origin requests", async () => {
  const data = await fixture();
  try {
    for (const headers of [{ Origin: "https://evil.example" }, { "Sec-Fetch-Site": "cross-site" }, { "X-TodayCommunity-Write": "" }]) {
      assert.equal((await request(data, { body: { post_id: 1, saved: true }, headers })).status, 403);
    }
    for (const body of ["{", "null", { post_id: -1, saved: true }, { post_id: 1, saved: "true" }, { post_id: 1.5, saved: true }]) {
      assert.equal((await request(data, { body })).status, 400);
    }
    assert.equal((await request(data, { body: { post_id: 999, saved: true } })).status, 404);
    assert.equal((await request(data, { body: "x".repeat(1025) })).status, 413);
    assert.equal((await request(data, { body: {}, headers: { "Content-Type": "text/plain" } })).status, 415);
    assert.equal((await request(data, { method: "DELETE" })).status, 405);
    for (const query of ["ids=", "ids=1,-2", `ids=${Array(101).fill(1).join(",")}`, "cursor=next:0:1", "cursor=oops", "cursor=next:9999999999999999:2"]) {
      assert.equal((await request(data, { query })).status, 400, query);
    }
  } finally { data.sqlite.close(); }
});

test("migration is additive, repeatable and matches the canonical schema", async () => {
  const data = await fixture();
  try {
    const migration = await readFile(new URL("../cloudflare/migrations/018_shared_favorites.sql", import.meta.url), "utf8");
    data.sqlite.exec("DROP TABLE shared_favorites");
    data.sqlite.exec(migration);
    data.sqlite.exec("INSERT INTO shared_favorites(post_id) VALUES(1)");
    data.sqlite.exec(migration);
    assert.equal(data.sqlite.prepare("SELECT count(*) AS n FROM shared_favorites").get().n, 1);
    assert.equal(data.sqlite.prepare("SELECT count(*) AS n FROM posts").get().n, 65);
  } finally { data.sqlite.close(); }
});
