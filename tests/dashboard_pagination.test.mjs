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
    window: { clearTimeout() {} },
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
    writeStateToControls = () => {};
    renderArchiveTabs = () => {};
    globalThis.archiveReview = {state, loadArchive, selectArchive, markArchiveChanged, archiveCacheBypassActive};
  `, runtime);
  return { ...runtime.archiveReview, calls, urls, events, successfulResponse };
}

function filterRequestHarness() {
  const timers = new Map();
  let timerId = 0;
  const requests = [];
  let saves = 0;
  const runtime = vm.createContext({
    document: { querySelector: () => ({ value: "" }) },
    URLSearchParams,
    window: {
      clearTimeout(id) { timers.delete(id); },
      setTimeout(callback) { timers.set(++timerId, callback); return timerId; },
    },
    recordRequest(url) { requests.push(url); },
    recordSave() { saves += 1; },
  });
  vm.runInContext(`${appWithoutInitialization}\n
    syncStateToUrl = () => {};
    loadArchive = () => recordRequest(buildApiUrl());
    queueArchiveFilterPreferenceSave = () => recordSave();
    elements.searchInput.value = state.search;
    elements.subjectSelect.value = state.subject;
    elements.upvotesInput.value = state.minUpvotes;
    elements.commentsInput.value = state.minComments;
    elements.sortSelect.value = state.sortBy;
    elements.pageSizeSelect.value = state.pageSize;
    globalThis.filterReview = { state, elements, buildApiUrl, handleFilterControlUpdate,
      handleSearchCompositionStart, handleSearchCompositionEnd };
  `, runtime);
  return {
    ...runtime.filterReview, requests,
    get saves() { return saves; },
    flush() { const pending = [...timers.values()]; timers.clear(); pending.forEach(fn => fn()); },
  };
}

test("a committed search issues one request even after blur and whitespace-only edits", () => {
  const app = filterRequestHarness();
  app.elements.searchInput.value = "게임";
  app.handleFilterControlUpdate({ type: "input", target: app.elements.searchInput });
  app.flush();
  assert.equal(app.requests.length, 1);
  Object.assign(app.state, { page: 3, cursor: "third-page" });
  app.handleFilterControlUpdate({ type: "change", target: app.elements.searchInput });
  app.flush();
  app.elements.searchInput.value = " 게임  ";
  app.handleFilterControlUpdate({ type: "input", target: app.elements.searchInput });
  app.flush();
  assert.equal(app.requests.length, 1);
  assert.equal(app.state.page, 3);
  assert.equal(app.state.cursor, "third-page");
  app.elements.searchInput.value = "게임 출시";
  app.handleFilterControlUpdate({ type: "input", target: app.elements.searchInput });
  app.flush();
  assert.equal(app.requests.length, 2);
  assert.equal(app.state.page, 1);
  assert.equal(app.state.cursor, "");
});

test("IME cancels a pending partial search and waits through pauses for composition end", () => {
  const app = filterRequestHarness();
  app.elements.searchInput.value = "g";
  app.handleFilterControlUpdate({ type: "input", target: app.elements.searchInput });
  app.handleSearchCompositionStart();
  app.flush();
  app.elements.searchInput.value = "게";
  app.handleFilterControlUpdate({ type: "input", isComposing: true, target: app.elements.searchInput });
  app.flush();
  assert.equal(app.requests.length, 0);
  app.elements.searchInput.value = "게임";
  app.handleSearchCompositionEnd({ target: app.elements.searchInput });
  app.handleFilterControlUpdate({ type: "input", isComposing: false, target: app.elements.searchInput });
  app.flush();
  app.handleFilterControlUpdate({ type: "change", target: app.elements.searchInput });
  app.flush();
  assert.equal(app.requests.length, 1);
  assert.equal(new URL(app.requests[0], "https://example.test").searchParams.get("q"), "게임");
});

test("unchanged mixed-archive controls preserve a cursor cleared by normalization", () => {
  const app = filterRequestHarness();
  Object.assign(app.state, { target: "all", page: 4, cursor: "fourth-page" });
  app.handleFilterControlUpdate({ target: app.elements.searchInput });
  app.flush();
  assert.equal(app.requests.length, 0);
  assert.equal(app.state.cursor, "fourth-page");
  assert.equal(app.state.page, 4);
});

test("equivalent archive exclusion order shares a URL and does not repeat preference writes", () => {
  const app = filterRequestHarness();
  Object.assign(app.state, {
    target: "all", archiveFilterLoaded: true,
    feedbackSession: { authentication: "authenticated" },
    excludedArchiveKeys: new Set(["game-news", "fmkorea-munich"]),
  });
  const keyBefore = app.buildApiUrl();
  let unchecked = ["fmkorea-munich", "game-news"];
  app.elements.archiveFilterOptions.querySelectorAll = () => unchecked.map(key => ({
    checked: false, dataset: { archiveFilterKey: key },
  }));
  const event = { target: { matches: () => true } };
  app.handleFilterControlUpdate(event);
  app.flush();
  assert.equal(app.buildApiUrl(), keyBefore);
  assert.equal(app.requests.length, 0);
  assert.equal(app.saves, 0);
  unchecked = ["game-news"];
  app.handleFilterControlUpdate(event);
  app.flush();
  app.handleFilterControlUpdate(event);
  app.flush();
  assert.equal(app.requests.length, 1);
  assert.equal(app.saves, 1);
});

test("reselecting the current archive reloads page one and resets filters and cursors", async () => {
  const app = archiveRequestHarness();
  Object.assign(app.state, {
    page: 3, cursor: "old-cursor", search: "query", subject: "subject",
    topicId: 101, minUpvotes: 10, minComments: 20, sortBy: "comments", pageSize: 50,
  });
  await app.selectArchive("dcinside-singularity");
  assert.equal(app.calls.length, 1);
  const query = new URL(app.urls[0], "https://example.test").searchParams;
  assert.equal(query.get("target"), "dcinside-singularity");
  assert.equal(query.get("page"), "1");
  assert.equal(query.get("page_size"), "30");
  assert.equal(query.get("sort"), "created_at");
  assert.equal(query.get("min_upvotes"), "0");
  assert.equal(query.get("min_comments"), "0");
  for (const key of ["q", "subject", "topic", "cursor"]) assert.equal(query.has(key), false);
  assert.equal(app.state.page, 1);
  assert.equal(app.state.cursor, "");
  assert.equal(app.state.focusArchiveTabAfterLoad, true);

  await app.selectArchive("dcinside-singularity");
  assert.equal(app.calls.length, 2, "page one can also be refreshed");
});

test("reselecting a tab replaces its pending request and ignores the older response", async () => {
  let finishFetch;
  let count = 0;
  const app = archiveRequestHarness({ fetchImpl: () => ++count === 1
    ? new Promise((resolve) => { finishFetch = resolve; })
    : Promise.resolve(app.successfulResponse) });
  const pending = app.loadArchive();
  await app.selectArchive("dcinside-singularity");
  assert.equal(app.calls.length, 2);
  assert.equal(app.calls[0].signal.aborted, true);
  const renders = app.events.filter((event) => event.type === "render").length;
  finishFetch(app.successfulResponse);
  await pending;
  assert.equal(app.events.filter((event) => event.type === "render").length, renders);
});

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
