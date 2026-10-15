import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = (await readFile(new URL("../dashboard/app.js", import.meta.url), "utf8"))
  .replace(/\binitialize\(\);\s*$/, "");

function harness(fetchImpl) {
  const context = vm.createContext({
    document: { querySelector: () => ({ querySelectorAll: () => [], setCustomValidity() {} }) },
    window: { __TODAY_COMMUNITY_ARCHIVE__: { posts: [{ title: "public fallback" }] } },
    fetch: fetchImpl, URLSearchParams, AbortController, TextEncoder, console,
  });
  vm.runInContext(`${source}
    const messages = [];
    showFeedbackToast = message => messages.push(message);
    renderLoadingState = () => {};
    render = () => {};
    globalThis.review = { state, messages, getAvailableArchives, normalizeContentState,
      loadFavoritesState, toggleFavorite, fetchArchivePage, syncFavoriteButton, loadArchive };
  `, context);
  context.review.state.feedbackSession = { authentication: "authenticated" };
  return context.review;
}

test("favorites navigation is secret-only and resets filters without erasing continuation", () => {
  const h = harness();
  for (const authentication of ["guest", "admin", "authenticated"]) {
    h.state.feedbackSession = { authentication };
    assert.equal(h.getAvailableArchives().some(item => item.archive_key === "favorites"), authentication === "authenticated");
  }
  Object.assign(h.state, { target: "favorites", page: 3, cursor: "next:30:3", search: "ignored", pageSize: 100, sortBy: "upvotes" });
  h.normalizeContentState();
  assert.equal(h.state.page, 3);
  assert.equal(h.state.cursor, "next:30:3");
  assert.equal(h.state.pageSize, 30);
  assert.equal(h.state.search, "");
  assert.equal(h.state.sortBy, "created_at");
});

test("a failed favorite list never substitutes a public archive snapshot", async () => {
  const h = harness(async () => ({ ok: false, status: 500, json: async () => ({ error: "unavailable" }) }));
  h.state.target = "favorites";
  await h.loadArchive();
  assert.equal(h.state.dataSource, "unavailable");
  assert.equal(h.state.archive.posts.length, 0);
  assert.equal(h.state.archive.error, "unavailable");
});

test("a slow favorite read cannot undo a newer mutation", async () => {
  let finish;
  let requests = 0;
  const h = harness(async () => {
    requests++;
    if (requests === 1) await new Promise(resolve => { finish = resolve; });
    return { ok: true, json: async () => ({ post_ids: requests === 1 ? [] : [1] }) };
  });
  const loading = h.loadFavoritesState([{ post_id: 1 }], new AbortController().signal);
  h.state.favorites.set(1, true);
  h.state.favoritesRevision++;
  finish();
  await loading;
  assert.equal(requests, 2);
  assert.equal(h.state.favorites.get(1), true);
});

test("an aborted read and an unsuccessful read cannot enable stale favorite controls", async () => {
  const h = harness(async () => ({ ok: false, status: 500, json: async () => ({ error: "failure" }) }));
  h.state.favorites.set(1, true);
  const controller = new AbortController();
  controller.abort();
  await h.loadFavoritesState([{ post_id: 1 }], controller.signal);
  assert.equal(h.state.favorites.get(1), true);
  await h.loadFavoritesState([{ post_id: 1 }], new AbortController().signal);
  assert.equal(h.state.favorites.has(1), false);
  assert.equal(h.messages.length, 1);
});

test("rapid repeated clicks issue one explicit-state write and failed saves leave the old value", async () => {
  let finish;
  const requests = [];
  const h = harness(async (_url, options) => {
    requests.push(options);
    await new Promise(resolve => { finish = resolve; });
    return { ok: false, status: 500, json: async () => ({ error: "save failed" }) };
  });
  h.state.favorites.set(1, false);
  const button = { isConnected: false };
  const first = h.toggleFavorite({ post_id: 1 }, button);
  await h.toggleFavorite({ post_id: 1 }, button);
  assert.equal(requests.length, 1);
  assert.deepEqual(JSON.parse(requests[0].body), { post_id: 1, saved: true });
  finish();
  await first;
  assert.equal(h.state.favorites.get(1), false);
  assert.equal(h.state.pendingFavorites.size, 0);
  assert.deepEqual(Array.from(h.messages), ["save failed"]);
});
