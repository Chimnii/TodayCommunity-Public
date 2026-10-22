import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test, { after } from "node:test";
import { Miniflare, convertV4MiniflareOptions } from "miniflare";

const pageSource = await readFile(new URL("../functions/_public-page.js", import.meta.url), "utf8");
const template = await readFile(new URL("../dashboard/index.html", import.meta.url), "utf8");
const archiveSource = await readFile(new URL("../functions/api/archive.js", import.meta.url), "utf8");
const { onRequestGet: loadArchive } = await import(`data:text/javascript;base64,${Buffer.from(archiveSource).toString("base64")}`);
const { publicPageState } = await import(`data:text/javascript;base64,${Buffer.from(pageSource).toString("base64")}`);

// Exercise the platform HTMLRewriter rather than substituting a regex HTML mock.
const runtime = new Miniflare(convertV4MiniflareOptions({
  modules: true,
  compatibilityDate: "2026-08-01",
  cf: false,
  telemetry: { enabled: false },
  bindings: { TEMPLATE: template },
  script: `${pageSource}
export default {
  async fetch(request, env, execution) {
    let apiRequest = null;
    let apiCalls = 0;
    const context = { request, env,
      waitUntil: promise => execution.waitUntil(promise),
      next: async templateRequest => templateRequest.headers.has("if-none-match")
        ? new Response(null, {status: 304}) : new Response(env.TEMPLATE, { headers: {
        "content-type": "text/html; charset=UTF-8", "etag": "old-static-etag",
        "cache-control": "public, max-age=3600", "last-modified": "Mon, 19 Oct 2026 00:00:00 GMT"
      } })
    };
    const response = await renderPublicPage(context, async inner => {
      apiCalls++;
      apiRequest = inner.request;
      const target = new URL(inner.request.url).searchParams.get("target");
      if (request.headers.has("x-test-failure")) return new Response("Unavailable", {status: 503});
      const payload = {
        target, archive: {archive_key: target, display_name: "fixture"}, archives: [],
        sources: [{source_key: "game-news-inven", site_name: "inven"}],
        summary: {total_posts: 2, latest_seen_at: "2026-10-19T02:00:00Z"},
        pagination: {page: 1, page_size: 30, has_next: false},
        runs: [],
        topic_trends: request.headers.has("x-test-no-topics") ? null : {
          window_hours: 24, generated_at: "2026-10-19T02:00:00Z",
          topics: request.headers.has("x-test-empty-topics") ? [] : [
            {topic_id: 17, label: '모델 비교 <img src=x onerror="bad()"> & 활용', post_count: 1234},
            {topic_id: 18, label: "AI 도구", post_count: -2},
            {topic_id: '19" onclick="bad()', label: "invalid identifier", post_count: 9},
            {topic_id: 0, label: "zero identifier", post_count: 9},
            {topic_id: 20, label: " ", post_count: 9},
          ],
        },
        posts: request.headers.has("x-test-empty") ? [] : [
          {post_id: 1, external_post_id: "123", archive_key: target === "all" ? "game-news" : target,
            source_key: "game-news-inven", subject: "release", title: '실제 저장 글 <script>alert("x")</script> & 제목',
            post_url: 'https://example.com/article?id=123&title="hello"', upvotes: 3, comments: 2,
            created_at: "2026-10-18T22:00:00Z"},
          {post_id: 2, archive_key: target, title: '</script><script id="injected">bad()</script>',
            post_url: "javascript:alert(1)", created_at: "invalid"}
        ]
      };
      const headers = {"content-type": "application/json"};
      if (!request.headers.has("x-test-legacy-cache")) headers["x-tc-expires-at"] = String(Date.now() + 4000);
      if (request.headers.has("x-test-expired")) headers["x-tc-expires-at"] = String(Date.now() - 1000);
      return new Response(JSON.stringify(payload), {headers});
    });
    const result = new Response(response.body, response);
    result.headers.set("x-test-api-url", apiRequest?.url || "not-called");
    result.headers.set("x-test-api-calls", String(apiCalls));
    result.headers.set("x-test-api-cookie", apiRequest?.headers.get("cookie") || "absent");
    result.headers.set("x-test-api-refresh", apiRequest?.headers.get("x-tc-refresh") || "absent");
    return result;
  }
}`,
}));
after(async () => runtime.dispose());

async function page(path = "/", headers = {}, origin = "https://todaycommunity.pages.dev") {
  const response = await runtime.dispatchFetch(`${origin}${path}`, { headers });
  return { response, html: await response.text() };
}

function bootstrap(html) {
  const match = html.match(/<script id="archive-bootstrap" type="application\/json">([\s\S]*?)<\/script>/);
  return match ? JSON.parse(match[1]) : null;
}

test("public archives share the site title while keeping distinct descriptions and canonical URLs", async () => {
  const targets = ["dcinside-singularity", "dcinside-agent-stack", "dcinside-zeus-pride", "fmkorea-munich", "game-news", "all"];
  const titles = new Set();
  const descriptions = new Set();
  for (const target of targets) {
    const { response, html } = await page(`/?target=${target}`);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("x-robots-tag"), "index, follow");
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.equal(response.headers.get("etag"), null);
    assert.equal(response.headers.get("last-modified"), null);
    const expectedCanonical = target === targets[0] ? "/" : `/?target=${target}`;
    assert.ok(html.includes(`rel="canonical" href="https://todaycommunity.pages.dev${expectedCanonical}"`));
    titles.add(html.match(/<title>([^<]+)<\/title>/)[1]);
    descriptions.add(html.match(/name="description"\s+content="([^"]+)"/)[1]);
    const expectedPage = publicPageState(new Request(`https://todaycommunity.pages.dev/?target=${target}`));
    assert.ok(html.includes(`<h2 id="archive-title" tabindex="-1">${expectedPage.heading}</h2>`));
    assert.ok(html.includes(`property="og:title" content="${expectedPage.heading} | 오늘의 커뮤니티"`));
    if (expectedPage.selection) {
      assert.ok(html.includes(`<span>${expectedPage.selection.description} <a class="selection-guide-link" href="${expectedPage.selection.url}">선별 기준</a></span>`));
      assert.ok(html.includes(`name="description" content="${expectedPage.description}"`));
    } else {
      assert.doesNotMatch(html, /class="selection-guide-link"/);
    }
    assert.match(html, /class="board-row post-row" role="row"/);
    assert.match(html, /href="https:\/\/example.com\/article\?id=123&amp;title=%22hello%22"/);
    assert.match(html, /aria-busy="false"/);
    assert.match(html, /26\.10\.19/); // Server dates use the product's Korean timezone.
    const seed = bootstrap(html);
    assert.equal(seed.version, 1);
    assert.equal(seed.payload.target, target);
    assert.equal(seed.api_url, `/api/archive?target=${target}&page=1&page_size=30&min_upvotes=0&min_comments=0&sort=created_at`);
    assert.equal(new URL(response.headers.get("x-test-api-url")).searchParams.get("page_size"), "30");
    assert.equal(response.headers.get("x-test-api-cookie"), "absent");
    if (target === "all") {
      assert.ok(html.indexOf('id="source-column-label"') < html.indexOf('id="subject-column-label"'));
      assert.match(html, /data-content-kind="mixed"/);
    }
  }
  assert.deepEqual([...titles], ["오늘의 커뮤니티"]);
  assert.equal(descriptions.size, 6);
  const root = await page();
  assert.equal(bootstrap(root.html).payload.target, targets[0]);
});

test("initial community HTML includes escaped topic names and counts from the same single public read", async () => {
  const { response, html } = await page();
  assert.equal(response.headers.get("x-test-api-calls"), "1");
  assert.match(html, /<aside[^>]+id="topic-panel"[^>]*>/);
  assert.doesNotMatch(html.match(/<aside[^>]+id="topic-panel"[^>]*>/)[0], /hidden/);
  assert.equal((html.match(/class="topic-entry"/g) || []).length, 2);
  assert.match(html, /data-topic-id="17" aria-pressed="false"/);
  assert.match(html, /모델 비교 &lt;img src=x onerror=&quot;bad\(\)&quot;&gt; &amp; 활용/);
  assert.match(html, /class="topic-count">\(1,234개\)<\/span>/);
  assert.match(html, /class="topic-count">\(0개\)<\/span>/);
  assert.doesNotMatch(html, /<img src=x|onclick="bad|data-topic-id="(?:0|19|20)"/);
  assert.match(html, /24시간 기준 · 10\. 19\. 11:00 갱신/);
  assert.equal(bootstrap(html).payload.topic_trends.topics[0].post_count, 1234);
  for (const [headers, message] of [
    [{"x-test-empty-topics": "1"}, "반복해서 다뤄진 토픽이 아직 없습니다."],
    [{"x-test-no-topics": "1"}, "첫 토픽 분석이 완료되면 여기에 표시됩니다."],
  ]) {
    const empty = await page("/", headers);
    assert.match(empty.html, new RegExp(`<p class="topic-empty" id="topic-empty"[^>]*>${message}</p>`));
    assert.doesNotMatch(empty.html.match(/<p[^>]+id="topic-empty"[^>]*>/)[0], /hidden/);
    assert.doesNotMatch(empty.html, /class="topic-entry"/);
  }
});

test("topics are not rendered for mixed, article, filtered, private or failed responses", async () => {
  for (const [path, headers] of [
    ["/?target=all", {}], ["/?target=game-news", {}], ["/?topic=17", {}],
    ["/", {cookie: "__Host-tc_authenticated=private"}], ["/", {"x-test-failure": "1"}],
  ]) {
    const { html } = await page(path, headers);
    assert.doesNotMatch(html, /class="topic-entry"/);
    assert.match(html.match(/<aside[^>]+id="topic-panel"[^>]*>/)[0], /hidden/);
  }
});

test("untrusted stored strings cannot escape a row, link attribute, or bootstrap JSON", async () => {
  const { html } = await page();
  assert.doesNotMatch(html, /<script id="injected">|<script>alert|href="javascript:/);
  assert.match(html, /&lt;script&gt;alert/);
  assert.match(html, /\\u003c\/script\\u003e/);
  assert.equal(bootstrap(html).payload.posts[1].title, '</script><script id="injected">bad()</script>');
});

test("private cookie/authorization requests never read or serialize archive data", async () => {
  for (const headers of [{ cookie: "__Host-tc_authenticated=private-secret" },
    { cookie: "other=value; __Host-tc_admin=private-secret" }, { authorization: "Bearer private-secret" }]) {
    const { response, html } = await page("/?target=all", headers);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("x-test-api-url"), "not-called");
    assert.equal(response.headers.get("x-robots-tag"), "noindex, follow");
    assert.match(html, /data-private-request="true"/);
    assert.equal(bootstrap(html), null);
    assert.doesNotMatch(html, /private-secret|실제 저장 글/);
  }
  const ordinary = await page("/", {cookie: "unrelated=value"});
  assert.ok(bootstrap(ordinary.html));
  assert.equal(ordinary.response.headers.get("x-test-api-cookie"), "absent");
});

test("filter/page/search URLs stay noindex and cannot receive mismatched default rows", async () => {
  for (const query of ["q=secret-search", "subject=private-subject", "min_upvotes=10", "min_comments=5",
    "page=2", "page_size=50", "cursor=private-cursor", "sort=comments", "topic=1",
    "exclude_archive=game-news", "utm_source=test", "target=all&target=game-news"]) {
    const { response, html } = await page(`/?${query}`);
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("x-test-api-url"), "not-called");
    assert.equal(response.headers.get("x-robots-tag"), "noindex, follow");
    assert.equal(bootstrap(html), null);
    assert.doesNotMatch(html, /secret-search|private-subject|private-cursor|실제 저장 글/);
  }
});

test("favorites, unknown targets and preview hosts cannot be indexed", async () => {
  for (const [query, expectedStatus] of [["favorites", 200], ["private-archive", 404], ["", 404], ["constructor", 404]]) {
    const { response, html } = await page(`/?target=${query}`);
    assert.equal(response.status, expectedStatus);
    assert.equal(response.headers.get("x-test-api-url"), "not-called");
    assert.equal(response.headers.get("x-robots-tag"), "noindex, nofollow");
    assert.equal(bootstrap(html), null);
  }
  const preview = await page("/?target=game-news", {}, "https://preview.todaycommunity.pages.dev");
  assert.equal(preview.response.headers.get("x-robots-tag"), "noindex, follow");
  assert.match(preview.html, /rel="canonical" href="https:\/\/todaycommunity.pages.dev\/\?target=game-news"/);
});

test("archive outages return a retryable 503 instead of indexing an empty success", async () => {
  const { response, html } = await page("/", { "x-test-failure": "1" });
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("retry-after"), "60");
  assert.equal(response.headers.get("x-robots-tag"), "noindex, follow");
  assert.equal(bootstrap(html), null);
  assert.match(html, /잠시 후 다시 시도/);
  const empty = await page("/", { "x-test-empty": "1" });
  assert.equal(empty.response.status, 200);
  assert.match(empty.html, /아직 저장된 글이 없습니다/);
});

test("bootstrap expiry never exceeds the existing API cache lifetime and refresh bypass survives", async () => {
  const started = Date.now();
  const { response, html } = await page("/", { "x-tc-refresh": "1" });
  assert.equal(response.headers.get("x-test-api-refresh"), "1");
  const seed = bootstrap(html);
  assert.ok(seed.expires_at >= started && seed.expires_at <= Date.now() + 4000);
  const expired = await page("/", { "x-test-expired": "1" });
  assert.ok(bootstrap(expired.html).expires_at < Date.now());
  const legacy = await page("/", { "x-test-legacy-cache": "1" });
  assert.equal(bootstrap(legacy.html).expires_at, 0);
});

test("page policy is independent of user agent and never copies request secrets into canonical", () => {
  const normal = publicPageState(new Request("https://todaycommunity.pages.dev/?target=game-news"));
  const crawler = publicPageState(new Request("https://todaycommunity.pages.dev/?target=game-news", { headers: { "user-agent": "Googlebot" } }));
  assert.deepEqual(normal, crawler);
  const filtered = publicPageState(new Request("https://todaycommunity.pages.dev/?target=game-news&q=secret"));
  assert.equal(filtered.canonical, normal.canonical);
  assert.equal(filtered.renderArchive, false);
});

test("conditional asset and HEAD requests keep the dynamic status and robots policy", async () => {
  const conditional = await page("/?target=game-news", { "if-none-match": "old-static-etag", range: "bytes=0-50" });
  assert.equal(conditional.response.status, 200);
  assert.ok(bootstrap(conditional.html));
  for (const [path, status] of [["/?target=game-news", 200], ["/?target=unknown", 404]]) {
    const response = await runtime.dispatchFetch(`https://todaycommunity.pages.dev${path}`, {method: "HEAD"});
    assert.equal(response.status, status);
    assert.equal(await response.text(), "");
    assert.equal(response.headers.get("x-test-api-url"), "not-called");
    assert.equal(response.headers.get("cache-control"), "private, no-store");
    assert.equal(response.headers.get("x-robots-tag"), status === 404 ? "noindex, nofollow" : "index, follow");
  }
});

test("API expiry is absolute, preserved on cache hits, and absent freshness is not invented", async () => {
  const schema = await readFile(new URL("../cloudflare/schema.sql", import.meta.url), "utf8");
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec(schema);
  const prepare = (sql, values = []) => ({
    bind(...args) { return prepare(sql, args); },
    async all() { return { results: sqlite.prepare(sql).all(...values).map(row => ({ ...row })) }; },
  });
  const oldCaches = globalThis.caches;
  const stored = new Map();
  globalThis.caches = { default: {
    async match(key) { return stored.get(key.url)?.clone(); },
    async put(key, value) { stored.set(key.url, value.clone()); },
  } };
  const context = { request: new Request("https://todaycommunity.pages.dev/api/archive?target=dcinside-singularity"),
    env: { DB: { prepare, batch: items => Promise.all(items.map(item => item.all())) } } };
  try {
    const started = Date.now();
    const first = await loadArchive(context);
    assert.equal(first.status, 200);
    const expiry = Number(first.headers.get("x-tc-expires-at"));
    assert.ok(expiry >= started && expiry <= Date.now() + 120_000);
    const second = await loadArchive(context);
    assert.equal(second.headers.get("x-tc-cache"), "hit");
    assert.equal(second.headers.get("x-tc-expires-at"), first.headers.get("x-tc-expires-at"));
    const bypass = await loadArchive({ ...context, request: new Request(context.request, { headers: { "x-tc-refresh": "1" } }) });
    assert.equal(bypass.headers.get("cache-control"), "no-store");
    assert.ok(Number(bypass.headers.get("x-tc-expires-at")) <= Date.now());
  } finally {
    sqlite.close();
    if (oldCaches === undefined) delete globalThis.caches; else globalThis.caches = oldCaches;
  }
});
