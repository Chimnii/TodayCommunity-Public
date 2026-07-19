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

  async first() {
    this.database.calls.push({ method: "first", sql: this.sql, values: this.values });

    if (this.sql.includes("FROM sources")) {
      return this.database.source;
    }
    if (this.sql.includes("AS total_posts")) {
      return this.database.summary;
    }
    if (this.sql.includes("AS filtered_posts")) {
      return { filtered_posts: this.database.filteredPosts };
    }

    throw new Error(`Unexpected first() query: ${this.sql}`);
  }

  async all() {
    this.database.calls.push({ method: "all", sql: this.sql, values: this.values });

    if (this.sql.includes("FROM crawl_runs")) {
      return { results: this.database.runs };
    }
    if (this.sql.includes("FROM posts")) {
      return { results: this.database.posts };
    }

    throw new Error(`Unexpected all() query: ${this.sql}`);
  }
}

class MockDatabase {
  constructor({
    totalPosts = 0,
    filteredPosts = totalPosts,
    subjectOptionsJson = "[]",
    posts = [],
    runs = [],
  } = {}) {
    this.calls = [];
    this.source = {
      source_key: "dcinside-singularity",
      site_name: "DCInside",
      board_name: "Singularity",
    };
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
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(body.target, "dcinside-singularity");
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

  const filteredCountCall = findCall(database, "AS filtered_posts", "first");
  assert.deepEqual(filteredCountCall.values, ["dcinside-singularity", 0, 0]);

  const summaryCall = findCall(database, "AS total_posts", "first");
  assert.match(summaryCall.sql, /json_group_array\(subject\)/);
  assert.match(summaryCall.sql, /SELECT DISTINCT TRIM\(subject\) AS subject/);
  assert.match(summaryCall.sql, /length\(TRIM\(subject\)\) <= 100/);
  assert.match(summaryCall.sql, /ORDER BY subject COLLATE NOCASE, subject LIMIT 100/);
  assert.deepEqual(summaryCall.values, ["dcinside-singularity", "dcinside-singularity"]);

  const postCall = findCall(database, "SELECT source_key, external_post_id", "all");
  assert.match(postCall.sql, /external_post_id, subject, title/);
  assert.match(postCall.sql, /ORDER BY created_at DESC, id DESC LIMIT \? OFFSET \?/);
  assert.deepEqual(postCall.values, ["dcinside-singularity", 0, 0, 30, 0]);
  assert.equal(body.posts[0].subject, "AI 소식");

  const runCall = findCall(database, "FROM crawl_runs", "all");
  assert.match(runCall.sql, /ORDER BY id DESC LIMIT 10/);
  assert.deepEqual(runCall.values, ["dcinside-singularity"]);
});

test("applies escaped title and numeric filters before paginating with a stable sort", async () => {
  const target = "board' OR 1=1 --";
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

  const selectedSubject = "AI 소식' OR 1=1 --";
  const expectedFilterBindings = [target, 4, 15, selectedSubject, "%100\\%\\_\\\\%"];
  const filteredCountCall = findCall(database, "AS filtered_posts", "first");
  assert.match(filteredCountCall.sql, /upvotes >= \? AND comments >= \?/);
  assert.match(filteredCountCall.sql, /subject = \?/);
  assert.ok(filteredCountCall.sql.includes("title LIKE ? ESCAPE '\\'"));
  assert.deepEqual(filteredCountCall.values, expectedFilterBindings);
  assert.ok(!filteredCountCall.sql.includes(selectedSubject));

  const postCall = findCall(database, "SELECT source_key, external_post_id", "all");
  assert.match(postCall.sql, /ORDER BY upvotes DESC, created_at DESC, id DESC/);
  assert.deepEqual(postCall.values, [...expectedFilterBindings, 20, 20]);
  assert.ok(!postCall.sql.includes(target));
  assert.ok(!postCall.sql.includes("100%_"));
});

test("clamps an out-of-range page to the last filtered page before querying rows", async () => {
  const database = new MockDatabase({
    totalPosts: 90,
    filteredPosts: 65,
    posts: makeRows(5, 805),
  });

  const { body } = await requestArchive(database, "?page=999&page_size=30");

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

  const postCall = findCall(database, "SELECT source_key, external_post_id", "all");
  assert.deepEqual(postCall.values, ["dcinside-singularity", 0, 0, 30, 60]);
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
      const postCall = findCall(database, "SELECT source_key, external_post_id", "all");

      assert.equal(body.pagination.page, 1);
      assert.equal(body.pagination.page_size, 100);
      assert.equal(body.pagination.total_pages, 0);
      assert.equal(body.pagination.visible_from, 0);
      assert.equal(body.pagination.visible_to, 0);
      assert.ok(postCall.sql.includes(expectedOrder));
      assert.ok(!postCall.sql.includes("DROP TABLE"));
      assert.deepEqual(postCall.values, [
        "dcinside-singularity",
        0,
        0,
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

test("keeps the existing JSON error response when D1 fails", async () => {
  const database = {
    prepare() {
      throw new Error("D1 unavailable");
    },
  };

  const { response, body } = await requestArchive(database);

  assert.equal(response.status, 500);
  assert.equal(response.headers.get("content-type"), "application/json; charset=UTF-8");
  assert.equal(body.error, "Failed to load archive data from D1.");
  assert.match(body.details, /D1 unavailable/);
});
