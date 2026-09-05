import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFile } from "node:fs/promises";
import test from "node:test";

const archiveSource = await readFile(new URL("../functions/api/archive.js", import.meta.url), "utf8");
const archiveModule = await import(
  `data:text/javascript;base64,${Buffer.from(archiveSource).toString("base64")}`
);
const { onRequestGet } = archiveModule;

const EMPTY_LATEST_TOPIC = {
  payload_json: JSON.stringify({
    version: 1,
    window_hours: 24,
    window_start: "2026-08-21T00:00:00Z",
    window_end: "2026-08-22T00:00:00Z",
    generated_at: "2026-08-22T00:00:00Z",
    summary: "최근 24시간에는 반복해서 다뤄진 주요 토픽이 아직 없습니다.",
    eligible_post_count: 0,
    analyzed_post_count: 0,
    topics: [],
  }),
};

function compactSql(sql) {
  return sql.replace(/\s+/g, " ").trim();
}

class MockStatement {
  constructor(database, sql) {
    this.database = database;
    this.sql = compactSql(sql);
    this.values = [];
  }

  bind(...values) {
    this.values = values;
    return this;
  }

  result() {
    if (
      this.sql.includes("FROM archives AS archive") &&
      this.sql.includes("LEFT JOIN archive_stats AS stats")
    ) {
      return {
        results: this.database.archiveStats.map((row) => {
          if (this.sql.includes("stats.subject_options_json")) {
            return row;
          }
          const { subject_options_json: _discardedOptions, ...withoutOptions } = row;
          return withoutOptions;
        }),
      };
    }
    if (
      this.sql.includes("SELECT archive_key, display_name, description, content_kind") &&
      this.sql.includes("FROM archives")
    ) {
      if (this.sql.includes("WHERE archive_key = ?")) {
        const archive = this.database.archives.find(
          (candidate) => candidate.archive_key === this.values[0]
        );
        return { results: archive ? [archive] : [] };
      }
      return { results: this.database.archives };
    }
    if (
      this.sql.includes("FROM sources") &&
      !this.sql.includes("JOIN sources") &&
      !this.sql.includes("FROM crawl_runs")
    ) {
      return {
        results: this.sql.includes("source_archive.is_public = 1")
          ? this.database.sources
          : this.database.sources.filter(
              (source) => source.archive_key === this.values[0]
            ),
      };
    }
    if (this.sql.includes("FROM crawl_runs")) {
      return { results: this.database.runs };
    }
    if (this.sql.includes("SELECT id AS topic_id, label")) {
      const topic = this.database.topics.find(
        (candidate) =>
          candidate.topic_id === this.values[0] &&
          candidate.archive_key === this.values[1]
      );
      return { results: topic ? [topic] : [] };
    }
    if (this.sql.includes("FROM community_topic_latest")) {
      return { results: this.database.topicLatest ? [this.database.topicLatest] : [] };
    }
    if (this.sql.includes("SELECT window_start, window_end, window_hours")) {
      return { results: this.database.topicSnapshot ? [this.database.topicSnapshot] : [] };
    }
    if (this.sql.includes("FROM community_topic_snapshot_items")) {
      return { results: this.database.topicItems };
    }
    if (this.sql.includes("FROM posts")) {
      return { results: this.database.posts };
    }

    throw new Error(`Unexpected query: ${this.sql}`);
  }

  async first() {
    this.database.calls.push({ method: "first", sql: this.sql, values: this.values });
    return this.result().results[0] ?? null;
  }

  async all() {
    this.database.calls.push({ method: "all", sql: this.sql, values: this.values });
    return this.result();
  }

  batchResult() {
    this.database.calls.push({ method: "batch", sql: this.sql, values: this.values });
    return this.result();
  }
}

class MockDatabase {
  constructor({
    totalPosts = 0,
    filteredPosts = totalPosts,
    subjectOptionsJson = "[]",
    posts = [],
    runs = [],
    archives,
    sources,
    topicLatest = EMPTY_LATEST_TOPIC,
    topicSnapshot = null,
    topicItems = [],
    topics = [],
    archiveStats = null,
  } = {}) {
    this.calls = [];
    this.batchRequests = [];
    this.archives = archives ?? [
      {
        archive_key: "dcinside-singularity",
        display_name: "특이점이 온다",
        description: "디시인사이드 특이점이 온다 갤러리 인기글",
        content_kind: "community",
        display_order: 10,
        updated_at: "2026-07-17T01:07:00Z",
      },
      {
        archive_key: "dcinside-agent-stack",
        display_name: "에이전트 스택",
        description: "디시인사이드 에이전트 스택 갤러리 인기글",
        content_kind: "community",
        display_order: 20,
        updated_at: "2026-07-17T01:07:00Z",
      },
      {
        archive_key: "dcinside-zeus-pride",
        display_name: "제우스 오만의 신",
        description: "디시인사이드 제우스 오만의 신 갤러리 인기글",
        content_kind: "community",
        display_order: 25,
        updated_at: "2026-08-28T01:07:00Z",
      },
      {
        archive_key: "fmkorea-munich",
        display_name: "뮌헨",
        description: "에펨코리아의 뮌헨 관련 인기글",
        content_kind: "community",
        display_order: 30,
        updated_at: "2026-07-17T01:07:00Z",
      },
      {
        archive_key: "game-news",
        display_name: "게임 뉴스",
        description: "게임 신작, 인터뷰와 업계 동향 기사",
        content_kind: "article",
        display_order: 40,
        updated_at: "2026-07-17T01:07:00Z",
      },
    ];
    this.sources = sources ?? [
      {
        source_key: "dcinside-singularity",
        archive_key: "dcinside-singularity",
        site_name: "DCInside",
        board_name: "Singularity",
      },
    ];
    const statsTarget = this.sources[0]?.archive_key || "dcinside-singularity";
    this.archiveStats = archiveStats ?? this.archives.map((archive) => ({
      ...archive,
      active_post_count: archive.archive_key === statsTarget ? totalPosts : 0,
      latest_seen_at: archive.archive_key === statsTarget
        ? "2026-07-17T01:07:00Z"
        : "",
      stats_version: archive.archive_key === statsTarget ? 7 : 0,
      subject_options_json: archive.archive_key === statsTarget
        ? subjectOptionsJson
        : "[]",
    }));
    this.summary = {
      total_posts: totalPosts,
      latest_seen_at: "2026-07-17T01:07:00Z",
      subject_options_json: subjectOptionsJson,
    };
    this.filteredPosts = filteredPosts;
    this.posts = posts;
    this.runs = runs;
    this.topicLatest = topicLatest;
    this.topicSnapshot = topicSnapshot;
    this.topicItems = topicItems;
    this.topics = topics;
  }

  prepare(sql) {
    return new MockStatement(this, sql);
  }

  async batch(statements) {
    this.batchRequests.push(statements);
    return statements.map((statement) => statement.batchResult());
  }
}

function makeRows(count, startingId = 1000) {
  return Array.from({ length: count }, (_, index) => ({
    cursor_id: startingId - index,
    external_post_id: String(startingId - index),
    subject: index === 0 ? "AI 소식" : "",
    title: `post ${startingId - index}`,
    created_at: new Date(Date.UTC(2026, 0, 1) + (startingId - index) * 1000).toISOString(),
    upvotes: (startingId - index) % 37,
    comments: (startingId - index) % 19,
  }));
}

async function requestArchive(database, search = "", headers = {}) {
  const response = await onRequestGet({
    request: new Request(`https://todaycommunity.pages.dev/api/archive${search}`, {
      headers,
    }),
    env: { DB: database },
  });
  return { response, body: await response.json() };
}

function findCall(database, fragment, method) {
  const call = database.calls.find(
    (candidate) => candidate.method === method && candidate.sql.includes(fragment)
  );
  assert.ok(call, `Expected ${method} query containing ${fragment}`);
  return call;
}

test("defaults to the first 30 globally counted posts and preserves recent runs", async () => {
  const database = new MockDatabase({
    totalPosts: 75,
    subjectOptionsJson: JSON.stringify([
      "일반",
      " AI 소식 ",
      "",
      "AI 소식",
      null,
      42,
      "로봇 연구",
    ]),
    posts: makeRows(30),
    runs: makeRows(10),
  });

  const { response, body } = await requestArchive(database);

  assert.equal(response.status, 200);
  assert.equal(
    response.headers.get("cache-control"),
    "public, max-age=15, s-maxage=120"
  );
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(body.target, "dcinside-singularity");
  assert.equal(body.archive.display_name, "특이점이 온다");
  assert.deepEqual(
    body.archives.map((archive) => archive.archive_key),
    [
      "dcinside-singularity",
      "dcinside-agent-stack",
      "dcinside-zeus-pride",
      "fmkorea-munich",
      "game-news",
      "all",
    ]
  );
  assert.equal(body.archive.content_kind, "community");
  assert.equal(
    body.archives.find((archive) => archive.archive_key === "game-news")?.content_kind,
    "article"
  );
  assert.equal(body.sources.length, 1);
  assert.deepEqual(body.source, body.sources[0]);
  assert.deepEqual(
    body.subject_options,
    ["일반", "AI 소식", "로봇 연구"].sort((left, right) =>
      left.localeCompare(right, "ko-KR")
    )
  );
  assert.deepEqual(body.summary, {
    total_posts: 75,
    filtered_posts: 75,
    latest_seen_at: "2026-07-17T01:07:00Z",
    stats_version: "dcinside-singularity:7",
    exported_posts: 30,
    recent_runs: 10,
  });
  assert.deepEqual(body.pagination, {
    mode: "numbered",
    page: 1,
    page_size: 30,
    total_pages: 3,
    visible_from: 1,
    visible_to: 30,
    has_previous: false,
    has_next: true,
    previous_cursor: null,
    next_cursor: null,
  });
  assert.equal(body.posts.length, 30);
  assert.equal(body.runs.length, 10);

  assert.equal(database.batchRequests.length, 1);
  assert.equal(database.batchRequests[0].length, 5);
  assert.equal(database.calls.filter((call) => call.method === "batch").length, 5);
  assert.equal(database.calls.filter((call) => call.method === "first").length, 0);
  assert.equal(database.calls.filter((call) => call.method === "all").length, 1);
  assert.ok(database.calls.every(({ sql }) => !/COUNT\(|MAX\(|DISTINCT/i.test(sql)));

  const statsCall = findCall(database, "LEFT JOIN archive_stats AS stats", "all");
  assert.match(statsCall.sql, /stats\.active_post_count/);
  assert.match(statsCall.sql, /stats\.subject_options_json/);
  assert.doesNotMatch(statsCall.sql, /FROM posts/);

  const postCall = findCall(database, "SELECT archive_key, source_key, external_post_id", "batch");
  assert.match(postCall.sql, /external_post_id, subject, title/);
  assert.match(postCall.sql, /status = 'active'/);
  assert.match(postCall.sql, /ORDER BY created_at DESC, id DESC LIMIT \? OFFSET \?/);
  assert.deepEqual(postCall.values, ["dcinside-singularity", 30, 0]);
  assert.equal(body.posts[0].subject, "AI 소식");

  const runCall = findCall(database, "FROM crawl_runs", "batch");
  assert.match(runCall.sql, /WITH archive_sources AS/);
  assert.match(runCall.sql, /FROM sources WHERE archive_key = \?/);
  assert.match(runCall.sql, /FROM archive_sources AS sources INNER JOIN crawl_runs AS runs/);
  assert.match(runCall.sql, /WHERE source_runs\.source_key = sources\.source_key/);
  assert.match(runCall.sql, /ORDER BY source_runs\.id DESC LIMIT 10/);
  assert.match(runCall.sql, /ORDER BY runs\.id DESC LIMIT 10/);
  assert.match(runCall.sql, /WHEN runs\.status IN \('failed', 'blocked'\)/);
  assert.match(runCall.sql, /AND TRIM\(runs\.error_message\) <> ''/);
  assert.match(runCall.sql, /END AS had_error/);
  assert.deepEqual(runCall.values, ["dcinside-singularity"]);
});

test("returns only a generic public marker for internal crawl errors", async () => {
  const database = new MockDatabase({
    runs: [
      {
        run_type: "hot_scan",
        status: "failed",
        had_error: 1,
        error_message: "provider diagnostic with internal identifier",
      },
    ],
  });

  const { body } = await requestArchive(database);

  assert.equal(body.runs[0].error_message, "수집 처리 중 오류가 발생했습니다.");
  assert.ok(!JSON.stringify(body).includes("provider diagnostic"));
  assert.ok(!Object.hasOwn(body.runs[0], "had_error"));
});

test("does not mislabel successful phase metadata as an error", async () => {
  const database = new MockDatabase({
    runs: [
      {
        run_type: "hot_scan",
        status: "completed",
        had_error: 0,
        error_message: '{"stop_reason":"lookback_reached"}',
      },
    ],
  });

  const { body } = await requestArchive(database);

  assert.equal(body.runs[0].status, "completed");
  assert.equal(body.runs[0].error_message, null);
  assert.ok(!JSON.stringify(body).includes("lookback_reached"));
  assert.ok(!Object.hasOwn(body.runs[0], "had_error"));
});

test("applies escaped title and numeric filters before paginating with a stable sort", async () => {
  const target = "dcinside-singularity";
  const database = new MockDatabase({
    totalPosts: 120,
    filteredPosts: 45,
    posts: makeRows(21, 900),
  });
  const params = new URLSearchParams({
    target,
    page: "2",
    page_size: "20",
    q: "100%_\\",
    min_upvotes: "4",
    min_comments: "15",
    subject: "  AI 소식' OR 1=1 --  ",
    sort: "upvotes",
  });

  const { body } = await requestArchive(database, `?${params}`);

  assert.equal(body.target, target);
  assert.equal(body.summary.total_posts, 120);
  assert.equal(body.summary.filtered_posts, null);
  assert.equal(body.pagination.mode, "sequential");
  assert.equal(body.pagination.page, 1);
  assert.equal(body.pagination.page_size, 20);
  assert.equal(body.pagination.total_pages, null);
  assert.equal(body.pagination.visible_from, 1);
  assert.equal(body.pagination.visible_to, 20);
  assert.equal(body.pagination.has_previous, false);
  assert.equal(body.pagination.has_next, true);
  assert.equal(body.pagination.previous_cursor, null);
  assert.equal(typeof body.pagination.next_cursor, "string");
  assert.equal(database.batchRequests.length, 1);
  assert.equal(database.batchRequests[0].length, 5);

  const selectedSubject = "AI 소식' OR 1=1 --";
  const expectedFilterBindings = [target, 4, 15, selectedSubject, "%100\\%\\_\\\\%"];
  assert.ok(database.calls.every(({ sql }) => !sql.includes("COUNT(")));
  const postCall = findCall(database, "SELECT archive_key, source_key, external_post_id", "batch");
  assert.match(postCall.sql, /ORDER BY upvotes DESC, created_at DESC, id DESC/);
  assert.match(postCall.sql, /upvotes >= \? AND comments >= \?/);
  assert.match(postCall.sql, /subject = \?/);
  assert.ok(postCall.sql.includes("title LIKE ? ESCAPE '\\'"));
  assert.deepEqual(postCall.values, [target, target, ...expectedFilterBindings]);
  assert.doesNotMatch(postCall.sql, /OFFSET/i);
  assert.ok(!postCall.sql.includes(target));
  assert.ok(!postCall.sql.includes("100%_"));
});

test("uses deterministic keyset cursors for next and previous filtered pages", async (t) => {
  for (const sort of ["created_at", "upvotes", "comments"]) {
    await t.test(sort, async () => {
      const database = new MockDatabase({ totalPosts: 50, posts: makeRows(3, 1000) });
      const base = new URLSearchParams({ q: "post", page_size: "2", sort });
      const first = await requestArchive(database, `?${base}`);

      assert.equal(first.body.pagination.page, 1);
      assert.equal(first.body.pagination.has_previous, false);
      assert.equal(first.body.pagination.has_next, true);
      assert.equal(typeof first.body.pagination.next_cursor, "string");
      assert.equal(first.body.posts.length, 2);
      assert.ok(first.body.posts.every((post) => !Object.hasOwn(post, "cursor_id")));
      const firstPostCall = database.calls.filter(
        ({ sql }) => sql.includes("SELECT archive_key, source_key, external_post_id")
      ).at(-1);
      assert.doesNotMatch(firstPostCall.sql, /OFFSET/i);

      database.posts = makeRows(3, 998);
      const nextParams = new URLSearchParams(base);
      nextParams.set("cursor", first.body.pagination.next_cursor);
      const second = await requestArchive(database, `?${nextParams}`);
      assert.equal(second.response.status, 200);
      assert.equal(second.body.pagination.page, 2);
      assert.equal(second.body.pagination.has_previous, true);
      assert.equal(typeof second.body.pagination.previous_cursor, "string");
      const nextPostCall = database.calls.filter(
        ({ sql }) => sql.includes("SELECT archive_key, source_key, external_post_id")
      ).at(-1);
      assert.doesNotMatch(nextPostCall.sql, /OFFSET/i);
      assert.match(nextPostCall.sql, /posts\.id\s*\) </);
      assert.match(nextPostCall.sql, new RegExp(`ORDER BY ${sort} DESC`));

      database.posts = makeRows(2, 1000).reverse();
      const previousParams = new URLSearchParams(base);
      previousParams.set("cursor", second.body.pagination.previous_cursor);
      const previous = await requestArchive(database, `?${previousParams}`);
      assert.equal(previous.response.status, 200);
      assert.equal(previous.body.pagination.page, 1);
      assert.equal(previous.body.pagination.has_previous, false);
      assert.equal(previous.body.pagination.has_next, true);
      assert.deepEqual(
        previous.body.posts.map((post) => post.external_post_id),
        ["1000", "999"]
      );
      const previousPostCall = database.calls.filter(
        ({ sql }) => sql.includes("SELECT archive_key, source_key, external_post_id")
      ).at(-1);
      assert.doesNotMatch(previousPostCall.sql, /OFFSET/i);
      assert.match(previousPostCall.sql, /posts\.id\s*\) >/);
      assert.match(previousPostCall.sql, new RegExp(`ORDER BY ${sort} ASC`));
    });
  }
});

test("rejects malformed, cross-filter, and unfiltered cursors before D1", async () => {
  const database = new MockDatabase({ totalPosts: 10, posts: makeRows(3) });
  const first = await requestArchive(database, "?q=post&page_size=2");
  const validCursor = first.body.pagination.next_cursor;
  const callsAfterFirst = database.calls.length;

  const crossFilter = new URLSearchParams({ q: "different", cursor: validCursor });
  const mismatched = await requestArchive(database, `?${crossFilter}`);
  assert.equal(mismatched.response.status, 400);
  assert.equal(mismatched.body.error, "Archive cursor is invalid or expired.");
  assert.equal(database.calls.length, callsAfterFirst);

  const malformed = await requestArchive(database, "?q=post&cursor=not-a-valid-payload");
  assert.equal(malformed.response.status, 400);
  assert.equal(database.calls.length, callsAfterFirst);

  const unfilteredDatabase = new MockDatabase();
  const unfiltered = await requestArchive(unfilteredDatabase, `?cursor=${validCursor}`);
  assert.equal(unfiltered.response.status, 400);
  assert.equal(unfiltered.body.error, "Archive cursor is invalid or expired.");
  assert.deepEqual(unfilteredDatabase.calls, []);
});

test("returns the latest topic snapshot and filters every matching archived post by topic", async () => {
  const target = "dcinside-singularity";
  const topicId = 17;
  const database = new MockDatabase({
    totalPosts: 12,
    filteredPosts: 4,
    posts: makeRows(4),
    topics: [{ topic_id: topicId, archive_key: target, label: "GPT-5.6 공개" }],
    topicLatest: null,
    topicSnapshot: {
      window_start: "2026-08-21T12:00:00Z",
      window_end: "2026-08-22T00:00:00Z",
      window_hours: 12,
      generated_at: "2026-08-22T00:01:00Z",
      summary_text: "최근 12시간에는 ‘GPT-5.6 공개’ 관련 글이 많이 다뤄졌습니다.",
      eligible_post_count: 12,
      analyzed_post_count: 11,
    },
    topicItems: [
      {
        topic_id: topicId,
        label: "GPT-5.6 공개",
        topic_rank: 1,
        post_count: 4,
        previous_post_count: 1,
        hotness_score: 61.5,
        trend_state: "rising",
        representative_posts_json: JSON.stringify([
          {
            external_post_id: "1000",
            title: "새 모델 공개",
            post_url: "https://example.com/post/1000",
            created_at: "2026-08-21T23:00:00Z",
          },
        ]),
      },
    ],
  });

  const { response, body } = await requestArchive(database, `?topic=${topicId}`);

  assert.equal(response.status, 200);
  assert.deepEqual(body.selected_topic, {
    topic_id: topicId,
    label: "GPT-5.6 공개",
  });
  assert.equal(body.topic_trends.window_hours, 12);
  assert.equal(body.topic_trends.analyzed_post_count, 11);
  assert.equal(body.topic_trends.topics[0].trend_state, "rising");
  assert.equal(
    body.topic_trends.topics[0].representative_posts[0].title,
    "새 모델 공개"
  );

  const postCall = findCall(database, "SELECT archive_key, source_key, external_post_id", "batch");
  assert.match(postCall.sql, /FROM community_post_topics AS selected_post_topic/);
  assert.match(postCall.sql, /selected_topic\.archive_key = posts\.archive_key/);
  assert.deepEqual(postCall.values, [target, target, target, topicId]);
  assert.doesNotMatch(postCall.sql, /OFFSET/i);
  assert.equal(body.summary.filtered_posts, null);
  assert.equal(body.pagination.mode, "sequential");
  assert.equal(body.pagination.total_pages, null);
  assert.equal(database.batchRequests.length, 2);
  assert.equal(database.batchRequests[1].length, 2);
  assert.ok(
    database.calls.some(({ sql }) => sql.includes("FROM community_topic_snapshot_items"))
  );
});

test("prefers the canonical latest topic row without reading legacy history", async () => {
  const topicId = 17;
  const database = new MockDatabase({
    topicLatest: {
      payload_json: JSON.stringify({
        version: 1,
        window_hours: 24,
        window_start: "2026-08-21T00:00:00Z",
        window_end: "2026-08-22T00:00:00Z",
        generated_at: "2026-08-22T00:01:00Z",
        summary: "저장 수치로 만든 기존 형식의 요약",
        eligible_post_count: 12,
        analyzed_post_count: 11,
        topics: [
          {
            topic_id: topicId,
            label: "GPT-5.6 공개",
            post_count: 4,
            previous_post_count: 1,
            hotness_score: 61.5,
            trend_state: "rising",
            representative_posts: [
              {
                external_post_id: "1001",
                title: "첫 번째 대표 글",
                post_url: "https://example.com/post/1001",
                created_at: "2026-08-21T23:30:00Z",
              },
              {
                external_post_id: "1000",
                title: "두 번째 대표 글",
                post_url: "https://example.com/post/1000",
                created_at: "2026-08-21T23:00:00Z",
              },
            ],
          },
        ],
      }),
    },
  });

  const { response, body } = await requestArchive(database);

  assert.equal(response.status, 200);
  assert.equal(body.topic_trends.window_hours, 24);
  assert.equal(body.topic_trends.topics[0].label, "GPT-5.6 공개");
  assert.equal(body.topic_trends.topics[0].post_count, 4);
  assert.deepEqual(
    body.topic_trends.topics[0].representative_posts.map((post) => post.title),
    ["첫 번째 대표 글", "두 번째 대표 글"]
  );
  assert.equal(database.batchRequests.length, 1);
  assert.ok(
    database.calls.some(({ sql }) => sql.includes("FROM community_topic_latest"))
  );
  assert.ok(
    database.calls.every(({ sql }) => !sql.includes("FROM community_topic_snapshot_items"))
  );
});

test("rejects malformed and cross-archive topic filters", async () => {
  const malformedDatabase = new MockDatabase();
  const malformed = await requestArchive(malformedDatabase, "?topic=1%20OR%201=1");
  assert.equal(malformed.response.status, 400);
  assert.deepEqual(malformedDatabase.calls, []);

  const unknownDatabase = new MockDatabase();
  const unknown = await requestArchive(unknownDatabase, "?topic=999");
  assert.equal(unknown.response.status, 400);
  assert.equal(unknown.body.error, "Unknown topic filter.");
});

test("rejects an unsupported target before querying D1", async () => {
  const database = new MockDatabase();
  const params = new URLSearchParams({ target: "board' OR 1=1 --" });

  const { response, body } = await requestArchive(database, `?${params}`);

  assert.equal(response.status, 400);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(body.error, "Unknown archive target.");
  assert.deepEqual(database.calls, []);
});

test("validates well-formed targets against public archives", async () => {
  const database = new MockDatabase();

  const { response, body } = await requestArchive(database, "?target=missing-archive");

  assert.equal(response.status, 400);
  assert.equal(body.error, "Unknown archive target.");
  assert.equal(database.batchRequests.length, 0);
  assert.equal(database.calls.length, 1);
  assert.equal(database.calls[0].method, "all");
  assert.match(database.calls[0].sql, /LEFT JOIN archive_stats AS stats/);
  assert.match(database.calls[0].sql, /archive\.is_public = 1/);
  assert.deepEqual(database.calls[0].values, []);
});

test("serves the Zeus gallery as its own public community archive", async () => {
  const target = "dcinside-zeus-pride";
  const sources = [
    {
      source_key: target,
      archive_key: target,
      site_name: "dcinside",
      board_name: "제우스 오만의 신 마이너 갤러리",
      board_url: "https://gall.dcinside.com/mgallery/board/lists/?id=zeusthegodofpride",
      min_upvotes: 3,
      min_comments: 0,
    },
  ];
  const database = new MockDatabase({ sources });

  const { response, body } = await requestArchive(database, `?target=${target}`);

  assert.equal(response.status, 200);
  assert.equal(body.target, target);
  assert.equal(body.archive.display_name, "제우스 오만의 신");
  assert.equal(body.archive.content_kind, "community");
  assert.deepEqual(body.sources, sources);
  assert.deepEqual(body.source, sources[0]);
});

test("combines multiple collection sources under one archive", async () => {
  const target = "fmkorea-munich";
  const sources = [
    {
      source_key: "fmkorea-best-munich-search",
      archive_key: target,
      site_name: "fmkorea",
      board_name: "포텐 터짐 '뮌헨' 검색",
    },
    {
      source_key: "fmkorea-best-bayern-search",
      archive_key: target,
      site_name: "fmkorea",
      board_name: "포텐 터짐 '바이에른' 검색",
    },
    {
      source_key: "fmkorea-bayern-board",
      archive_key: target,
      site_name: "fmkorea",
      board_name: "해외축구 바이에른 게시판",
    },
  ];
  const database = new MockDatabase({
    sources,
    totalPosts: 2,
    posts: makeRows(2),
    runs: [
      {
        source_key: sources[2].source_key,
        board_name: sources[2].board_name,
        status: "completed",
        had_error: 0,
      },
    ],
  });

  const { response, body } = await requestArchive(database, `?target=${target}`);

  assert.equal(response.status, 200);
  assert.equal(body.target, target);
  assert.equal(body.archive.display_name, "뮌헨");
  assert.deepEqual(body.sources, sources);
  assert.deepEqual(body.source, sources[0]);
  assert.equal(body.runs[0].source_key, "fmkorea-bayern-board");
  assert.equal(body.runs[0].board_name, "해외축구 바이에른 게시판");

  assert.ok(database.calls.every(({ sql }) => !/COUNT\(|MAX\(|DISTINCT/i.test(sql)));
  const sourceCall = findCall(database, "FROM sources", "batch");
  assert.deepEqual(sourceCall.values, [target]);
});

test("combines every public archive in the virtual all target", async () => {
  const sources = [
    {
      source_key: "dcinside-singularity",
      archive_key: "dcinside-singularity",
      site_name: "dcinside",
      board_name: "특이점이 온다",
    },
    {
      source_key: "game-news-inven",
      archive_key: "game-news",
      site_name: "inven",
      board_name: "인벤",
    },
  ];
  const gameNewsKey = "a".repeat(32);
  const posts = [
    {
      archive_key: "dcinside-singularity",
      source_key: "dcinside-singularity",
      external_post_id: "123",
      subject: "일반",
      title: "커뮤니티 글",
    },
    {
      archive_key: "game-news",
      source_key: "game-news-inven",
      external_post_id: gameNewsKey,
      subject: "business",
      title: "게임 뉴스 기사",
    },
  ];
  const database = new MockDatabase({
    sources,
    totalPosts: posts.length,
    posts,
  });

  const { response, body } = await requestArchive(database, "?target=all");

  assert.equal(response.status, 200);
  assert.equal(response.headers.get("cache-control"), "public, max-age=15, s-maxage=120");
  assert.equal(body.target, "all");
  assert.equal(body.archive.archive_key, "all");
  assert.equal(body.archive.content_kind, "mixed");
  assert.equal(body.archives.at(-1).archive_key, "all");
  assert.deepEqual(body.sources, sources);
  assert.equal(body.topic_trends, null);
  assert.equal(body.selected_topic, null);
  assert.equal(body.posts[0].feedback_key, undefined);
  assert.equal(body.posts[1].feedback_key, gameNewsKey);
  assert.equal(database.calls.filter((call) => call.method === "first").length, 0);

  const sourceCall = findCall(database, "FROM sources", "batch");
  assert.match(sourceCall.sql, /INNER JOIN archives AS source_archive/);
  assert.match(sourceCall.sql, /source_archive\.is_public = 1/);
  assert.deepEqual(sourceCall.values, []);

  const statsCall = findCall(database, "LEFT JOIN archive_stats AS stats", "all");
  assert.doesNotMatch(statsCall.sql, /FROM posts/);
  assert.ok(database.calls.every(({ sql }) => !/COUNT\(|MAX\(|DISTINCT/i.test(sql)));

  const postCall = findCall(
    database,
    "SELECT archive_key, source_key, external_post_id",
    "batch"
  );
  assert.match(postCall.sql, /public_archive\.archive_key = posts\.archive_key/);
  assert.deepEqual(postCall.values, database.archiveStats.flatMap((row) => [row.archive_key, row.archive_key]));
  assert.equal(body.summary.total_posts, 2);
  assert.equal(body.summary.filtered_posts, 2);
  assert.equal(body.pagination.mode, "sequential");
});

test("excludes selected archives before counting and paginating the all target", async () => {
  const database = new MockDatabase({
    totalPosts: 20,
    filteredPosts: 7,
    posts: makeRows(7),
  });
  database.archiveStats = database.archiveStats.map((row) => ({
    ...row,
    active_post_count: row.archive_key === "dcinside-singularity"
      ? 7
      : row.archive_key === "game-news"
        ? 8
        : row.archive_key === "dcinside-agent-stack"
          ? 5
          : 0,
    stats_version: 1,
  }));
  const params = new URLSearchParams({ target: "all" });
  params.append("exclude_archive", "game-news");
  params.append("exclude_archive", "dcinside-agent-stack");
  params.append("exclude_archive", "game-news");

  const { response, body } = await requestArchive(database, `?${params}`);

  assert.equal(response.status, 200);
  assert.equal(body.summary.filtered_posts, 7);
  assert.equal(body.summary.total_posts, 7);
  assert.equal(body.pagination.mode, "sequential");
  assert.ok(database.calls.every(({ sql }) => !/COUNT\(|MAX\(|DISTINCT/i.test(sql)));

  const postCall = findCall(
    database,
    "SELECT archive_key, source_key, external_post_id",
    "batch"
  );
  assert.match(postCall.sql, /public_archive\.archive_key = posts\.archive_key/);
  assert.match(postCall.sql, /public_archive\.archive_key NOT IN \(\?, \?\)/);
  assert.deepEqual(postCall.values, [
    ...database.archiveStats.filter((row) => !["game-news", "dcinside-agent-stack"].includes(row.archive_key)).flatMap((row) => [row.archive_key, row.archive_key]),
    "game-news",
    "dcinside-agent-stack",
  ]);
});

test("rejects malformed or cross-target archive exclusions before querying D1", async () => {
  for (const search of [
    "?target=all&exclude_archive=",
    "?target=all&exclude_archive=all",
    "?target=all&exclude_archive=bad%20key",
    "?target=game-news&exclude_archive=dcinside-singularity",
  ]) {
    const database = new MockDatabase();
    const { response } = await requestArchive(database, search);
    assert.equal(response.status, 400, search);
    assert.deepEqual(database.calls, [], search);
  }

  const database = new MockDatabase();
  const params = new URLSearchParams({ target: "all" });
  for (let index = 0; index < 51; index += 1) {
    params.append("exclude_archive", `archive-${index}`);
  }
  const { response } = await requestArchive(database, `?${params}`);
  assert.equal(response.status, 400);
  assert.deepEqual(database.calls, []);
});

test("rejects topic filters for the virtual all target before querying D1", async () => {
  const database = new MockDatabase();

  const { response, body } = await requestArchive(database, "?target=all&topic=1");

  assert.equal(response.status, 400);
  assert.equal(body.error, "Topic filters are unavailable for this archive.");
  assert.deepEqual(database.calls, []);
});

test("serves article archives from published posts without exposing curation data", async () => {
  const target = "game-news";
  const sources = [
    {
      source_key: "game-news-inven",
      archive_key: target,
      site_name: "inven",
      board_name: "인벤",
    },
    {
      source_key: "game-news-thisisgame",
      archive_key: target,
      site_name: "thisisgame",
      board_name: "디스이즈게임",
    },
  ];
  const database = new MockDatabase({
    sources,
    totalPosts: 1,
    posts: [
      {
        archive_key: target,
        source_key: sources[0].source_key,
        external_post_id: "a".repeat(32),
        subject: "업계 동향",
        title: "게임 산업 기사",
        post_url: "https://www.inven.co.kr/webzine/news/?news=1",
        created_at: "2026-08-11T00:00:00Z",
        upvotes: 0,
        comments: 0,
        qualifies_by: "llm-include",
      },
    ],
  });

  const { response, body } = await requestArchive(database, `?target=${target}`);

  assert.equal(response.status, 200);
  assert.equal(body.archive.content_kind, "article");
  assert.equal(body.posts.length, 1);
  assert.equal(body.posts[0].qualifies_by, "llm-include");
  assert.equal(body.posts[0].feedback_key, "a".repeat(32));
  assert.equal(response.headers.get("cache-control"), "public, max-age=15, s-maxage=120");
  assert.equal(JSON.stringify(body).includes("game_news_candidates"), false);
  assert.equal(JSON.stringify(body).includes("model_id"), false);
  assert.ok(database.calls.every(({ sql }) => !sql.includes("game_news_")));
});

test("normalizes cache keys and reuses a short-lived edge response", async () => {
  const database = new MockDatabase({ totalPosts: 1, posts: makeRows(1) });
  const stored = new Map();
  const cache = {
    async match(request) {
      const response = stored.get(request.url);
      return response?.clone();
    },
    async put(request, response) {
      stored.set(request.url, response.clone());
    },
  };
  const originalCaches = globalThis.caches;
  globalThis.caches = { default: cache };

  try {
    const first = await requestArchive(database, "?page=01&page_size=30&junk=ignored");
    const callsAfterFirst = database.calls.length;
    const second = await requestArchive({
      prepare() {
        throw new Error("cache hit must not prepare D1");
      },
    }, "?page=1&page_size=030");

    assert.equal(first.response.status, 200);
    assert.equal(second.response.status, 200);
    assert.deepEqual(second.body, first.body);
    assert.equal(database.calls.length, callsAfterFirst);
    assert.equal(stored.size, 1);
  } finally {
    if (originalCaches === undefined) {
      delete globalThis.caches;
    } else {
      globalThis.caches = originalCaches;
    }
  }
});

test("serves all and game-news public cache hits without touching D1", async (t) => {
  for (const target of ["all", "game-news"]) {
    await t.test(target, async () => {
      const sources = target === "game-news"
        ? [{ source_key: "game-news-inven", archive_key: "game-news" }]
        : undefined;
      const database = new MockDatabase({ totalPosts: 1, posts: makeRows(1), sources });
      const stored = new Map();
      const originalCaches = globalThis.caches;
      globalThis.caches = {
        default: {
          async match(request) {
            return stored.get(request.url)?.clone();
          },
          async put(request, response) {
            stored.set(request.url, response.clone());
          },
        },
      };

      try {
        const search = `?target=${target}`;
        const first = await requestArchive(database, search);
        const callsAfterMiss = database.calls.length;
        const second = await requestArchive(database, search);
        assert.equal(first.response.status, 200);
        assert.equal(second.response.status, 200);
        assert.deepEqual(second.body, first.body);
        assert.equal(database.calls.length, callsAfterMiss);
      } finally {
        if (originalCaches === undefined) delete globalThis.caches;
        else globalThis.caches = originalCaches;
      }
    });
  }
});

test("shares public edge caching for owner sessions", async () => {
  const database = new MockDatabase({ totalPosts: 1, posts: makeRows(1) });
  let cacheCalls = 0;
  const originalCaches = globalThis.caches;
  globalThis.caches = {
    default: {
      async match() {
        cacheCalls += 1;
        return null;
      },
      async put() {
        cacheCalls += 1;
      },
    },
  };

  try {
    const { response } = await requestArchive(
      database,
      "?target=game-news",
      { cookie: "__Host-tc_authenticated=session-token" }
    );
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "public, max-age=15, s-maxage=120");
    assert.equal(cacheCalls, 2);
    assert.ok(database.calls.length > 0);
  } finally {
    if (originalCaches === undefined) delete globalThis.caches;
    else globalThis.caches = originalCaches;
  }
});

test("clamps an out-of-range unfiltered page using the stats total", async () => {
  const database = new MockDatabase({
    totalPosts: 90,
    filteredPosts: 65,
    posts: makeRows(5, 805),
  });

  const { body } = await requestArchive(database, "?page=999&page_size=30");

  assert.equal(database.batchRequests.length, 1);
  assert.equal(database.batchRequests[0].length, 5);
  assert.equal(body.summary.filtered_posts, 90);
  assert.deepEqual(body.pagination, {
    mode: "numbered",
    page: 3,
    page_size: 30,
    total_pages: 3,
    visible_from: 61,
    visible_to: 65,
    has_previous: true,
    has_next: false,
    previous_cursor: null,
    next_cursor: null,
  });

  const postCall = findCall(database, "SELECT archive_key, source_key, external_post_id", "batch");
  assert.deepEqual(postCall.values, ["dcinside-singularity", 30, 0]);
});

test("bounds query controls and allowlists every sort expression", async (t) => {
  const sortCases = [
    ["created_at", "ORDER BY created_at DESC, id DESC"],
    ["upvotes", "ORDER BY upvotes DESC, created_at DESC, id DESC"],
    ["comments", "ORDER BY comments DESC, created_at DESC, id DESC"],
    ["title; DROP TABLE posts", "ORDER BY created_at DESC, id DESC"],
  ];

  for (const [sort, expectedOrder] of sortCases) {
    await t.test(sort, async () => {
      const database = new MockDatabase();
      const params = new URLSearchParams({
        page: "0",
        page_size: "999",
        q: "x".repeat(48),
        min_upvotes: "-1",
        min_comments: "3.5",
        subject: "😀".repeat(100),
        sort,
      });

      const { body } = await requestArchive(database, `?${params}`);
      const postCall = findCall(
        database,
        "SELECT archive_key, source_key, external_post_id",
        "batch"
      );

      assert.equal(body.pagination.page, 1);
      assert.equal(body.pagination.page_size, 100);
      assert.equal(body.pagination.mode, "sequential");
      assert.equal(body.pagination.total_pages, null);
      assert.equal(body.pagination.visible_from, 0);
      assert.equal(body.pagination.visible_to, 0);
      assert.ok(postCall.sql.includes(expectedOrder));
      assert.ok(!postCall.sql.includes("DROP TABLE"));
      assert.deepEqual(postCall.values, [
        "dcinside-singularity",
        "dcinside-singularity",
        "dcinside-singularity",
        "😀".repeat(100),
        `%${"x".repeat(48)}%`,
      ]);
      assert.doesNotMatch(postCall.sql, /OFFSET/i);
    });
  }
});

test("rejects an overlength subject before querying D1", async () => {
  const database = new MockDatabase();
  const params = new URLSearchParams({ subject: "😀".repeat(101) });

  const { response, body } = await requestArchive(database, `?${params}`);

  assert.equal(response.status, 400);
  assert.match(body.error, /100 characters or fewer/);
  assert.deepEqual(database.calls, []);
});

test("bounds and sanitizes aggregated subject options", async () => {
  const values = [
    ...Array.from({ length: 105 }, (_, index) => `말머리 ${String(index).padStart(3, "0")}`),
    " ",
    "중복",
    " 중복 ",
    "😀".repeat(101),
  ];
  const database = new MockDatabase({ subjectOptionsJson: JSON.stringify(values) });

  const { body } = await requestArchive(database);

  assert.equal(body.subject_options.length, 100);
  assert.equal(new Set(body.subject_options).size, body.subject_options.length);
  assert.ok(body.subject_options.every((subject) => subject.trim() === subject && subject.length));
  assert.ok(body.subject_options.every((subject) => Array.from(subject).length <= 100));
  assert.ok(!body.subject_options.includes("😀".repeat(101)));
  assert.deepEqual(
    body.subject_options,
    [...body.subject_options].sort((left, right) => left.localeCompare(right, "ko-KR"))
  );
});

test("returns no subject options when D1 aggregation is malformed", async () => {
  const database = new MockDatabase({ subjectOptionsJson: "{not-json" });

  const { response, body } = await requestArchive(database);

  assert.equal(response.status, 200);
  assert.deepEqual(body.subject_options, []);
});

test("merges capped subject JSON from fixed archive stats rows for the all view", async () => {
  const database = new MockDatabase();
  database.archiveStats = database.archiveStats.map((row, index) => ({
    ...row,
    subject_options_json: JSON.stringify([
      `공통 말머리`,
      ...Array.from({ length: 30 }, (_, optionIndex) => (
        `${index}-${String(optionIndex).padStart(2, "0")}`
      )),
    ]),
  }));

  const { body } = await requestArchive(database, "?target=all");

  assert.equal(body.subject_options.length, 100);
  assert.equal(new Set(body.subject_options).size, 100);
  const statsCall = findCall(database, "LEFT JOIN archive_stats AS stats", "all");
  assert.match(statsCall.sql, /stats\.subject_options_json/);
  assert.doesNotMatch(statsCall.sql, /archive_subject_stats|FROM posts/i);
  assert.equal(
    database.calls.filter(({ sql }) => sql.includes("archive_stats AS stats")).length,
    1
  );
});

test("returns a generic non-cacheable response when D1 fails", async () => {
  const database = {
    prepare() {
      throw new Error("D1 unavailable");
    },
  };
  const originalConsoleError = console.error;
  let loggedError = [];
  console.error = (...values) => {
    loggedError = values;
  };

  let response;
  let body;
  try {
    ({ response, body } = await requestArchive(database));
  } finally {
    console.error = originalConsoleError;
  }

  assert.equal(response.status, 500);
  assert.equal(response.headers.get("content-type"), "application/json; charset=UTF-8");
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(body.error, "Failed to load archive data from D1.");
  assert.ok(!Object.hasOwn(body, "details"));
  assert.ok(!JSON.stringify(body).includes("D1 unavailable"));
  assert.deepEqual(loggedError[1], { name: "Error" });
});

test("returns a generic non-cacheable response when the D1 read batch fails", async () => {
  const database = new MockDatabase();
  database.batch = async () => {
    throw new Error("D1 batch unavailable");
  };
  const originalConsoleError = console.error;
  let loggedError = [];
  console.error = (...values) => {
    loggedError = values;
  };

  let response;
  let body;
  try {
    ({ response, body } = await requestArchive(database));
  } finally {
    console.error = originalConsoleError;
  }

  assert.equal(response.status, 500);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(body.error, "Failed to load archive data from D1.");
  assert.ok(!JSON.stringify(body).includes("D1 batch unavailable"));
  assert.equal(database.calls.length, 1);
  assert.equal(database.calls[0].method, "all");
  assert.deepEqual(loggedError[1], { name: "Error" });
});
