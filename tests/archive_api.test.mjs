import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFile } from "node:fs/promises";
import test from "node:test";

const archiveSource = await readFile(new URL("../functions/api/archive.js", import.meta.url), "utf8");
const archiveModule = await import(
  `data:text/javascript;base64,${Buffer.from(archiveSource).toString("base64")}`
);
const { onRequestGet } = archiveModule;

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
    if (this.sql.includes("FROM archives")) {
      if (this.sql.includes("WHERE archive_key = ?")) {
        const archive = this.database.archives.find(
          (candidate) => candidate.archive_key === this.values[0]
        );
        return { results: archive ? [archive] : [] };
      }
      return { results: this.database.archives };
    }
    if (this.sql.includes("AS total_posts")) {
      return { results: [this.database.summary] };
    }
    if (this.sql.includes("AS filtered_posts")) {
      return { results: [{ filtered_posts: this.database.filteredPosts }] };
    }
    if (this.sql.includes("FROM sources") && !this.sql.includes("JOIN sources")) {
      return {
        results: this.database.sources.filter(
          (source) => source.archive_key === this.values[0]
        ),
      };
    }
    if (this.sql.includes("FROM crawl_runs")) {
      return { results: this.database.runs };
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
  } = {}) {
    this.calls = [];
    this.batchRequests = [];
    this.archives = archives ?? [
      {
        archive_key: "dcinside-singularity",
        display_name: "특이점이 온다",
        description: "디시인사이드 특이점이 온다 갤러리 인기글",
        display_order: 10,
        updated_at: "2026-07-17T01:07:00Z",
      },
      {
        archive_key: "dcinside-agent-stack",
        display_name: "에이전트 스택",
        description: "디시인사이드 에이전트 스택 갤러리 인기글",
        display_order: 20,
        updated_at: "2026-07-17T01:07:00Z",
      },
      {
        archive_key: "fmkorea-munich",
        display_name: "뮌헨",
        description: "에펨코리아의 뮌헨 관련 인기글",
        display_order: 30,
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
    this.summary = {
      total_posts: totalPosts,
      latest_seen_at: "2026-07-17T01:07:00Z",
      subject_options_json: subjectOptionsJson,
    };
    this.filteredPosts = filteredPosts;
    this.posts = posts;
    this.runs = runs;
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
    external_post_id: String(startingId - index),
    subject: index === 0 ? "AI 소식" : "",
    title: `post ${startingId - index}`,
  }));
}

async function requestArchive(database, search = "") {
  const response = await onRequestGet({
    request: new Request(`https://todaycommunity.pages.dev/api/archive${search}`),
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
    "public, max-age=15, s-maxage=300"
  );
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(body.target, "dcinside-singularity");
  assert.equal(body.archive.display_name, "특이점이 온다");
  assert.deepEqual(
    body.archives.map((archive) => archive.archive_key),
    ["dcinside-singularity", "dcinside-agent-stack", "fmkorea-munich"]
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
    exported_posts: 30,
    recent_runs: 10,
  });
  assert.deepEqual(body.pagination, {
    page: 1,
    page_size: 30,
    total_pages: 3,
    visible_from: 1,
    visible_to: 30,
    has_previous: false,
    has_next: true,
  });
  assert.equal(body.posts.length, 30);
  assert.equal(body.runs.length, 10);

  assert.equal(database.batchRequests.length, 1);
  assert.equal(database.batchRequests[0].length, 6);
  assert.equal(database.calls.filter((call) => call.method === "batch").length, 6);
  assert.equal(database.calls.filter((call) => call.method === "first").length, 1);
  assert.equal(database.calls.filter((call) => call.method === "all").length, 0);

  const filteredCountCall = findCall(database, "AS filtered_posts", "batch");
  assert.deepEqual(filteredCountCall.values, ["dcinside-singularity"]);
  assert.doesNotMatch(filteredCountCall.sql, /upvotes >= \?|comments >= \?/);

  const summaryCall = findCall(database, "AS total_posts", "batch");
  assert.match(summaryCall.sql, /json_group_array\(subject\)/);
  assert.match(summaryCall.sql, /SELECT DISTINCT TRIM\(subject\) AS subject/);
  assert.match(summaryCall.sql, /length\(TRIM\(subject\)\) <= 100/);
  assert.match(summaryCall.sql, /ORDER BY subject COLLATE NOCASE, subject LIMIT 100/);
  assert.deepEqual(summaryCall.values, ["dcinside-singularity", "dcinside-singularity"]);

  const postCall = findCall(database, "SELECT archive_key, source_key, external_post_id", "batch");
  assert.match(postCall.sql, /external_post_id, subject, title/);
  assert.match(postCall.sql, /ORDER BY created_at DESC, id DESC LIMIT \? OFFSET \?/);
  assert.deepEqual(postCall.values, ["dcinside-singularity", 30, 0]);
  assert.equal(body.posts[0].subject, "AI 소식");

  const runCall = findCall(database, "FROM crawl_runs", "batch");
  assert.match(runCall.sql, /INNER JOIN sources ON sources\.source_key = runs\.source_key/);
  assert.match(runCall.sql, /WHERE sources\.archive_key = \?/);
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
    posts: makeRows(20, 900),
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
  assert.equal(body.summary.filtered_posts, 45);
  assert.deepEqual(body.pagination, {
    page: 2,
    page_size: 20,
    total_pages: 3,
    visible_from: 21,
    visible_to: 40,
    has_previous: true,
    has_next: true,
  });
  assert.equal(database.batchRequests.length, 1);
  assert.equal(database.batchRequests[0].length, 5);

  const selectedSubject = "AI 소식' OR 1=1 --";
  const expectedFilterBindings = [target, 4, 15, selectedSubject, "%100\\%\\_\\\\%"];
  const filteredCountCall = findCall(database, "AS filtered_posts", "batch");
  assert.match(filteredCountCall.sql, /upvotes >= \? AND comments >= \?/);
  assert.match(filteredCountCall.sql, /subject = \?/);
  assert.ok(filteredCountCall.sql.includes("title LIKE ? ESCAPE '\\'"));
  assert.deepEqual(filteredCountCall.values, expectedFilterBindings);
  assert.ok(!filteredCountCall.sql.includes(selectedSubject));

  const postCall = findCall(database, "SELECT archive_key, source_key, external_post_id", "all");
  assert.match(postCall.sql, /ORDER BY upvotes DESC, created_at DESC, id DESC/);
  assert.deepEqual(postCall.values, [...expectedFilterBindings, 20, 20]);
  assert.ok(!postCall.sql.includes(target));
  assert.ok(!postCall.sql.includes("100%_"));
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
  assert.equal(database.calls[0].method, "first");
  assert.match(database.calls[0].sql, /FROM archives/);
  assert.match(database.calls[0].sql, /WHERE archive_key = \? AND is_public = 1/);
  assert.deepEqual(database.calls[0].values, ["missing-archive"]);
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

  const countCall = findCall(database, "AS filtered_posts", "batch");
  assert.deepEqual(countCall.values, [target]);
  assert.doesNotMatch(countCall.sql, /upvotes >= \?|comments >= \?/);
  const sourceCall = findCall(database, "FROM sources", "batch");
  assert.deepEqual(sourceCall.values, [target]);
});

test("normalizes cache keys and reuses a five-minute edge response", async () => {
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
    const second = await requestArchive(database, "?page=1&page_size=030");

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

test("clamps an out-of-range page to the last filtered page before querying rows", async () => {
  const database = new MockDatabase({
    totalPosts: 90,
    filteredPosts: 65,
    posts: makeRows(5, 805),
  });

  const { body } = await requestArchive(database, "?page=999&page_size=30");

  assert.equal(database.batchRequests.length, 1);
  assert.equal(database.batchRequests[0].length, 5);
  assert.equal(body.summary.filtered_posts, 65);
  assert.deepEqual(body.pagination, {
    page: 3,
    page_size: 30,
    total_pages: 3,
    visible_from: 61,
    visible_to: 65,
    has_previous: true,
    has_next: false,
  });

  const postCall = findCall(database, "SELECT archive_key, source_key, external_post_id", "all");
  assert.deepEqual(postCall.values, ["dcinside-singularity", 30, 60]);
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
        q: "x".repeat(120),
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
      assert.equal(body.pagination.total_pages, 0);
      assert.equal(body.pagination.visible_from, 0);
      assert.equal(body.pagination.visible_to, 0);
      assert.ok(postCall.sql.includes(expectedOrder));
      assert.ok(!postCall.sql.includes("DROP TABLE"));
      assert.deepEqual(postCall.values, [
        "dcinside-singularity",
        "😀".repeat(100),
        `%${"x".repeat(100)}%`,
        100,
        0,
      ]);
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
  assert.match(String(loggedError[1]), /D1 unavailable/);
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
  assert.equal(database.calls[0].method, "first");
  assert.match(String(loggedError[1]), /D1 batch unavailable/);
});
