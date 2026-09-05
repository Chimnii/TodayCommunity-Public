import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const appUrl = new URL("../dashboard/app.js", import.meta.url);
const appSource = await readFile(appUrl, "utf8");
const appWithoutInitialization = appSource.replace(/\binitialize\(\);\s*$/, "");

assert.notEqual(
  appWithoutInitialization,
  appSource,
  "The dashboard test harness must remove the final initialize() call"
);

const context = {
  document: {
    querySelector() {
      return {};
    },
  },
};

vm.runInNewContext(
  `${appWithoutInitialization}\nglobalThis.__dashboardPaginationFunctions = {\n  getPageSequence: typeof getPageSequence === "function" ? getPageSequence : undefined,\n  parsePageJump: typeof parsePageJump === "function" ? parsePageJump : undefined,\n  normalizeSignedInteger: typeof normalizeSignedInteger === "function" ? normalizeSignedInteger : undefined,\n  createSubjectPreview: typeof createSubjectPreview === "function" ? createSubjectPreview : undefined,\n  splitSubjectGraphemes: typeof splitSubjectGraphemes === "function" ? splitSubjectGraphemes : undefined,\n  getArticleSourceLabel: typeof getArticleSourceLabel === "function" ? getArticleSourceLabel : undefined,\n  getArticleSubjectLabel: typeof getArticleSubjectLabel === "function" ? getArticleSubjectLabel : undefined,\n  normalizePagination: typeof normalizePagination === "function" ? normalizePagination : undefined,\n};`,
  context,
  { filename: appUrl.pathname }
);

const {
  getPageSequence,
  parsePageJump,
  normalizeSignedInteger,
  createSubjectPreview,
  splitSubjectGraphemes,
  getArticleSourceLabel,
  getArticleSubjectLabel,
  normalizePagination,
} =
  context.__dashboardPaginationFunctions;

function pageSequence(currentPage, totalPages) {
  return Array.from(getPageSequence(currentPage, totalPages));
}

test("loads the dashboard's pagination helpers without running initialize", () => {
  assert.equal(typeof getPageSequence, "function");
  assert.equal(typeof parsePageJump, "function");
  assert.equal(typeof normalizeSignedInteger, "function");
  assert.equal(typeof normalizePagination, "function");
});

function archiveRequestHarness({ storage, fetchImpl, now = () => 1000 } = {}) {
  const calls = [];
  const urls = [];
  const events = [];
  const successfulResponse = {
    ok: true,
    json: async () => ({ target: "dcinside-singularity", posts: [], pagination: { page: 1 } }),
  };
  const runtime = vm.createContext({
    document: { querySelector() {
      return {
        setCustomValidity(value) { events.push({ type: "validity", value }); },
        reportValidity() { events.push({ type: "report-validity" }); },
      };
    } },
    window: {},
    localStorage: storage,
    TextEncoder,
    URLSearchParams,
    AbortController,
    Date: class extends Date { static now() { return now(); } },
    events,
    fetch(url, options) {
      calls.push(options);
      urls.push(url);
      return fetchImpl ? fetchImpl(url, options) : Promise.resolve(successfulResponse);
    },
  });
  vm.runInContext(`${appWithoutInitialization}\n
    renderLoadingState = () => events.push({type: 'loading'});
    render = () => events.push({type: 'render', source: state.dataSource});
    setFiltersExpanded = () => {};
    withArchiveCatalog = value => value;
    syncStateToUrl = () => {};
    globalThis.archiveReview = {state, loadArchive, markArchiveChanged, archiveCacheBypassActive};
  `, runtime);
  return { ...runtime.archiveReview, calls, urls, events, successfulResponse };
}

test("an invalid initial search renders an input error and recovers after correction", async () => {
  const app = archiveRequestHarness();
  app.state.search = "가".repeat(17);
  await app.loadArchive();
  assert.equal(app.calls.length, 0);
  assert.equal(app.state.activeRequest, null);
  assert.equal(app.state.dataSource, "unavailable");
  assert.equal(app.state.archive.input_error, true);
  assert.match(app.state.archive.error, /16자 이내로 줄여 주세요/);
  assert.ok(app.events.some((event) => event.type === "render"));

  app.state.search = "가".repeat(16);
  await app.loadArchive();
  assert.equal(app.calls.length, 1);
  assert.equal(app.state.dataSource, "live");
  assert.equal(app.state.activeRequest, null);
  assert.equal(app.events.filter((event) => event.type === "validity").at(-1).value, "");
});

test("an invalid edit aborts the pending list and renders instead of leaving loading active", async () => {
  let finishFetch;
  const app = archiveRequestHarness({ fetchImpl: () => new Promise((resolve) => { finishFetch = resolve; }) });
  const pending = app.loadArchive();
  app.state.search = "가".repeat(17);
  await app.loadArchive();
  assert.equal(app.calls[0].signal.aborted, true);
  assert.equal(app.state.activeRequest, null);
  const rendered = app.events.filter((event) => event.type === "render").length;
  assert.equal(rendered, 1);
  assert.equal(app.state.archive.input_error, true);

  finishFetch(app.successfulResponse);
  await pending;
  assert.equal(app.events.filter((event) => event.type === "render").length, rendered);
  assert.equal(app.state.dataSource, "unavailable");
});

test("visibility refresh expiry survives reloads and is shared with already open tabs", async () => {
  const stored = new Map();
  const storage = {
    getItem: (key) => stored.get(key) ?? null,
    setItem: (key, value) => stored.set(key, value),
  };
  let now = 1000;
  const options = { storage, now: () => now };
  const first = archiveRequestHarness(options);
  const alreadyOpenTab = archiveRequestHarness(options);
  first.markArchiveChanged();
  assert.deepEqual([...stored], [["tc-archive-refresh-until", "136000"]]);
  const reloaded = archiveRequestHarness(options);
  for (const app of [first, alreadyOpenTab, reloaded]) {
    await app.loadArchive();
    assert.equal(app.calls[0].cache, "no-store");
    assert.equal(app.calls[0].headers["x-tc-refresh"], "1");
  }

  now = 136001;
  await reloaded.loadArchive();
  assert.equal(reloaded.calls.at(-1).cache, "default");
  assert.equal(reloaded.calls.at(-1).headers["x-tc-refresh"], undefined);
});

test("an aborted HTTP error cannot replace a newer successful archive response", async () => {
  let requestCount = 0;
  let finishBody;
  let notifyBodyStarted;
  const bodyStarted = new Promise((resolve) => { notifyBodyStarted = resolve; });
  const app = archiveRequestHarness({
    fetchImpl: async () => {
      requestCount += 1;
      if (requestCount > 1) return app.successfulResponse;
      return {
        ok: false,
        status: 400,
        json() {
          notifyBodyStarted();
          return new Promise((resolve) => { finishBody = resolve; });
        },
      };
    },
  });
  const oldRequest = app.loadArchive();
  await bodyStarted;
  app.state.search = "new query";
  await app.loadArchive();
  assert.equal(app.state.dataSource, "live");
  const successfulArchive = app.state.archive;
  finishBody({ error: "old input failure" });
  await oldRequest;
  assert.equal(app.state.dataSource, "live");
  assert.equal(app.state.archive, successfulArchive);
});

test("blocked browser storage preserves current-tab visibility refresh", async () => {
  const unavailable = () => { throw new Error("storage unavailable"); };
  const app = archiveRequestHarness({ storage: { getItem: unavailable, setItem: unavailable } });
  app.markArchiveChanged();
  await app.loadArchive();
  assert.equal(app.calls[0].cache, "no-store");
  assert.equal(app.calls[0].headers["x-tc-refresh"], "1");
});

test("normalizePagination keeps cursor navigation without inventing a total", () => {
  assert.deepEqual(
    { ...normalizePagination({
      mode: "sequential",
      page: 3,
      page_size: 20,
      total_pages: null,
      quick_page_count: null,
      visible_from: 41,
      visible_to: 60,
      has_previous: true,
      has_next: true,
      previous_cursor: "previous-token",
      next_cursor: "next-token",
    }, null, 20) },
    {
      mode: "sequential",
      page: 3,
      page_size: 20,
      total_pages: null,
      quick_page_count: null,
      visible_from: 41,
      visible_to: 60,
      has_previous: true,
      has_next: true,
      previous_cursor: "previous-token",
      next_cursor: "next-token",
    }
  );
});

test("normalizeSignedInteger preserves valid negative recommendation counts", () => {
  assert.equal(normalizeSignedInteger(-7, 0), -7);
  assert.equal(normalizeSignedInteger("-12", 0), -12);
  assert.equal(normalizeSignedInteger("1,200", 0), 0);
  assert.equal(normalizeSignedInteger("3.5", 0), 0);
  assert.equal(normalizeSignedInteger("not-a-number", 0), 0);
});

test("createSubjectPreview counts combined emoji as one grapheme for any preview limit", () => {
  assert.equal(createSubjectPreview("☕작업잡담", 5), "☕작업잡담");
  assert.equal(createSubjectPreview("☕작업잡담", 3), "☕작업");
  assert.equal(createSubjectPreview("👨‍👩‍👧‍👦AI잡담", 5), "👨‍👩‍👧‍👦AI잡담");
  assert.equal(createSubjectPreview("👨‍👩‍👧‍👦AI잡담", 3), "👨‍👩‍👧‍👦AI");
  assert.equal(createSubjectPreview("양자 컴퓨팅", 3), "양자 컴");
});

test("splitSubjectGraphemes preserves combined emoji without Intl.Segmenter", () => {
  assert.deepEqual(
    Array.from(splitSubjectGraphemes("👨‍👩‍👧‍👦AI잡담")),
    ["👨‍👩‍👧‍👦", "A", "I", "잡", "담"]
  );
  assert.deepEqual(Array.from(splitSubjectGraphemes("👍🏽소식")), ["👍🏽", "소", "식"]);
  assert.deepEqual(Array.from(splitSubjectGraphemes("🇰🇷AI")), ["🇰🇷", "A", "I"]);
});

test("getArticleSourceLabel uses known aliases and future site identifiers", () => {
  const sources = [
    { source_key: "game-news-inven", site_name: "inven" },
    { source_key: "game-news-thisisgame", site_name: "thisisgame" },
    { source_key: "game-news-gamemeca", site_name: "gm" },
    { source_key: "game-news-gameinsight", site_name: "gi" },
  ];

  assert.equal(
    getArticleSourceLabel({ source_key: "game-news-inven" }, sources),
    "inv"
  );
  assert.equal(
    getArticleSourceLabel({ source_key: "game-news-thisisgame" }, sources),
    "tig"
  );
  assert.equal(
    getArticleSourceLabel({ source_key: "game-news-gamemeca" }, sources),
    "gm"
  );
  assert.equal(
    getArticleSourceLabel({ source_key: "game-news-gameinsight" }, sources),
    "gi"
  );
  assert.equal(
    getArticleSourceLabel({ source_key: "game-news-gamefocus" }),
    "gam"
  );
});

test("getArticleSubjectLabel shortens only the requested game-news topics", () => {
  assert.equal(getArticleSubjectLabel("business"), "biz");
  assert.equal(getArticleSubjectLabel("development"), "dev");
  assert.equal(getArticleSubjectLabel("release"), "launch");
  assert.equal(getArticleSubjectLabel("technology"), "tech");
  assert.equal(getArticleSubjectLabel("other"), "etc");
  assert.equal(getArticleSubjectLabel("store"), "store");
  assert.equal(getArticleSubjectLabel("platform"), "store");
  assert.equal(getArticleSubjectLabel("policy"), "policy");
  assert.equal(getArticleSubjectLabel("esports"), "esports");
});

test("getPageSequence exposes a seven-page window around middle pages", () => {
  const cases = [
    [1, 20, [1, 2, 3, 4, "ellipsis", 20]],
    [5, 20, [1, 2, 3, 4, 5, 6, 7, 8, "ellipsis", 20]],
    [6, 20, [1, 2, 3, 4, 5, 6, 7, 8, 9, "ellipsis", 20]],
    [10, 20, [1, "ellipsis", 7, 8, 9, 10, 11, 12, 13, "ellipsis", 20]],
    [16, 20, [1, "ellipsis", 13, 14, 15, 16, 17, 18, 19, 20]],
    [20, 20, [1, "ellipsis", 17, 18, 19, 20]],
  ];

  for (const [currentPage, totalPages, expected] of cases) {
    assert.deepEqual(
      pageSequence(currentPage, totalPages),
      expected,
      `Unexpected page sequence for page ${currentPage} of ${totalPages}`
    );
  }
});

test("getPageSequence includes every page when the total is small", () => {
  for (let totalPages = 1; totalPages <= 7; totalPages += 1) {
    const expected = Array.from({ length: totalPages }, (_, index) => index + 1);

    for (let currentPage = 1; currentPage <= totalPages; currentPage += 1) {
      assert.deepEqual(pageSequence(currentPage, totalPages), expected);
    }
  }
});

test("getPageSequence always returns ordered, unique, in-range page numbers", () => {
  for (let totalPages = 1; totalPages <= 50; totalPages += 1) {
    for (let currentPage = 1; currentPage <= totalPages; currentPage += 1) {
      const numbers = pageSequence(currentPage, totalPages).filter(
        (entry) => typeof entry === "number"
      );

      assert.deepEqual(numbers, [...numbers].sort((left, right) => left - right));
      assert.equal(numbers.length, new Set(numbers).size);
      assert.ok(numbers.every((page) => Number.isInteger(page) && page >= 1 && page <= totalPages));
    }
  }
});

test("parsePageJump accepts integer strings and numbers within the available pages", () => {
  for (const [value, expected] of [
    ["1", 1],
    ["7", 7],
    ["20", 20],
    [1, 1],
    [7, 7],
    [20, 20],
  ]) {
    assert.equal(parsePageJump(value, 20), expected);
  }
});

test("parsePageJump rejects empty, non-integer, non-numeric, and out-of-range values", () => {
  for (const value of [
    "",
    "   ",
    0,
    "0",
    -1,
    "-1",
    1.5,
    "1.5",
    "page 5",
    Number.NaN,
    Number.POSITIVE_INFINITY,
    21,
    "21",
  ]) {
    assert.equal(parsePageJump(value, 20), null, `Expected ${String(value)} to be rejected`);
  }
});


function filteredPageResponse(url, lastPage = 8) {
  const params = new URL(url, "https://example.com").searchParams;
  const page = Number((params.get("cursor") || "cursor-1").split("-")[1]);
  return { ok: true, json: async () => ({
    target: "dcinside-singularity", posts: [{ title: params.get("q") }],
    pagination: { mode: "sequential", page, page_size: 30, has_previous: page > 1,
      has_next: page < lastPage, previous_cursor: page > 1 ? `cursor-${page - 1}` : null,
      next_cursor: page < lastPage ? `cursor-${page + 1}` : null },
  }) };
}

test("filtered quick jumps walk at most five windows and reuse only matching fresh boundaries", async () => {
  let now = 1000;
  const app = archiveRequestHarness({ now: () => now, fetchImpl: url => filteredPageResponse(url) });
  app.state.search = "post";
  app.state.page = 5;
  await app.loadArchive();
  assert.equal(app.calls.length, 5);
  assert.equal(app.state.page, 5);
  assert.equal(app.state.cursor, "cursor-5");
  app.state.page = 3;
  app.state.cursor = "";
  await app.loadArchive();
  assert.equal(app.calls.length, 6);
  assert.equal(new URL(app.urls.at(-1), "https://example.com").searchParams.get("cursor"), "cursor-3");
  app.state.sortBy = "comments";
  app.state.cursor = "";
  await app.loadArchive();
  assert.equal(app.calls.length, 9, "sort changes discard earlier boundaries");
  now += 16_000;
  app.state.cursor = "";
  await app.loadArchive();
  assert.equal(app.calls.length, 12, "expired boundaries are rebuilt");
});

test("filtered quick jumps stop at an earlier real end and update the URL state", async () => {
  const app = archiveRequestHarness({ fetchImpl: url => filteredPageResponse(url, 2) });
  app.state.search = "post";
  app.state.page = 5;
  await app.loadArchive();
  assert.equal(app.calls.length, 2);
  assert.equal(app.state.page, 2);
  assert.equal(app.state.cursor, "cursor-2");
  assert.equal(app.state.archive.pagination.has_next, false);
});

test("changing filters aborts an in-flight quick jump without showing its late result", async () => {
  let finishSecond;
  let enteredSecond;
  const second = new Promise(resolve => { enteredSecond = resolve; });
  const app = archiveRequestHarness({ fetchImpl: url => {
    const params = new URL(url, "https://example.com").searchParams;
    if (params.get("q") === "old" && params.get("cursor") === "cursor-2") {
      enteredSecond();
      return new Promise(resolve => { finishSecond = () => resolve(filteredPageResponse(url)); });
    }
    return filteredPageResponse(url);
  } });
  app.state.search = "old";
  app.state.page = 5;
  const pending = app.loadArchive();
  await second;
  app.state.search = "new";
  app.state.page = 1;
  app.state.cursor = "";
  await app.loadArchive();
  finishSecond();
  await pending;
  assert.equal(app.calls.length, 3);
  assert.equal(app.state.page, 1);
  assert.equal(app.state.archive.posts[0].title, "new");
});
