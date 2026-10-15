import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";

export async function loadModule(path, replacements = []) {
  let source = await readFile(new URL(path, import.meta.url), "utf8");
  for (const [from, to] of replacements) source = source.replace(from, to);
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}

const authUrl = await loadModule("../../functions/api/_auth.js");
export const auth = await import(authUrl);
export const api = await import(await loadModule("../../functions/api/favorites.js", [['"./_auth.js"', `"${authUrl}"`]]));
const schema = await readFile(new URL("../../cloudflare/schema.sql", import.meta.url), "utf8");

export async function fixture(count = 65) {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec("PRAGMA foreign_keys = ON");
  sqlite.exec(schema);
  sqlite.exec(`INSERT INTO sources(source_key, archive_key, site_name, board_name, board_url)
    VALUES ('fixture', 'dcinside-singularity', 'fixture', 'fixture', 'https://example.com')`);
  const insert = sqlite.prepare(`INSERT INTO posts (
    id, source_key, archive_key, external_post_id, canonical_post_key, title, post_url,
    created_at, created_at_raw, fetched_at, first_seen_at, last_seen_at, qualifies_by, subject, upvotes, comments
  ) VALUES (?, 'fixture', ?, ?, ?, ?, 'https://example.com/post',
    '2026-10-15T00:00:00Z', '2026-10-15T00:00:00Z', '2026-10-15T00:00:00Z',
    '2026-10-15T00:00:00Z', '2026-10-15T00:00:00Z', 'fixture', '정보', 10, 3)`);
  for (let id = 1; id <= count; id++) {
    insert.run(id, id % 2 ? "dcinside-singularity" : "game-news", String(id), `fixture:${id}`,
      `즐겨찾기 테스트 ${id} — 모바일에서도 제목과 별표가 겹치지 않는지 확인하는 긴 제목`);
  }
  sqlite.exec(`UPDATE archive_stats SET active_post_count =
    (SELECT count(*) FROM posts WHERE posts.archive_key = archive_stats.archive_key)`);
  const link = sqlite.prepare("INSERT INTO auth_secret_links(id,label,token_hash,created_at) VALUES(?,?,?,?)");
  link.run(1, "fixture one", "a".repeat(64), "2026-01-01T00:00:00Z");
  link.run(2, "fixture two", "b".repeat(64), "2026-01-01T00:00:00Z");
  const queries = [];
  const prepare = (sql, values = []) => ({
    bind(...args) { return prepare(sql, args); },
    async all() {
      const results = sqlite.prepare(sql).all(...values).map(row => ({ ...row }));
      queries.push({ sql, values, count: results.length });
      return { results };
    },
    async run() { return sqlite.prepare(sql).run(...values); },
  });
  const env = {
    DB: { prepare, batch: items => Promise.all(items.map(item => item.all())) },
    TC_AUTH_SESSION_SECRET: auth.encodeBase64Url(new Uint8Array(32).fill(17)),
  };
  const cookies = { guest: "" };
  for (const id of [1, 2]) cookies[`secret${id}`] = `${auth.AUTHENTICATED_COOKIE}=${await auth.createSessionToken(env, "authenticated", id)}`;
  cookies.admin = `${auth.ADMIN_COOKIE}=${await auth.createSessionToken(env, "admin")}`;
  return { sqlite, env, cookies, queries };
}

export async function request(data, { who = "secret1", query = "", body, method = body === undefined ? "GET" : "POST", headers = {} } = {}) {
  const result = await api.onRequest({
    env: data.env,
    request: new Request(`https://example.com/api/favorites${query ? `?${query}` : ""}`, {
      method,
      headers: { Cookie: data.cookies[who], ...(body === undefined ? {} : {
        "Content-Type": "application/json", "X-TodayCommunity-Write": "1", Origin: "https://example.com",
      }), ...headers },
      ...(body === undefined ? {} : { body: typeof body === "string" ? body : JSON.stringify(body) }),
    }),
  });
  return { status: result.status, headers: result.headers, body: await result.json() };
}
