import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = (await readFile(new URL("../dashboard/app.js", import.meta.url), "utf8"))
  .replace(/\binitialize\(\);\s*$/, "");
const guest = { authentication: "guest", actor: null, capabilities: { rate: false, manage_auth: false } };

function harness(fetchImpl, { origin = "https://todaycommunity.pages.dev", width = 390 } = {}) {
  const scripts = [];
  const calls = [];
  const slot = { hidden: true, dataset: {} };
  let context;
  const mount = {
    children: [],
    replaceChildren() { this.children = []; },
    append(tag) {
      this.children.push(tag);
      tag.parentElement = this;
      context.document.currentScript = tag;
      vm.runInContext(tag.textContent, context);
      context.document.currentScript = null;
    },
  };
  const document = {
    querySelector(selector) {
      return selector === "#guest-ad" ? slot : selector === "#guest-ad-content" ? mount
        : selector === "#aclib" ? scripts.find(script => script.id === "aclib") || null : {};
    },
    getElementById: id => id === "guest-ad" ? slot : null,
    createElement: () => ({}),
    head: { append: script => scripts.push(script) },
  };
  const aclib = { runBanner(options) { calls.push({ ...options, parent: document.currentScript.parentElement }); } };
  context = vm.createContext({
    document, window: { location: { origin },
      matchMedia: query => ({ matches: width >= Number(query.match(/min-width: (\d+)px/)[1]) }), aclib },
    aclib, fetch: fetchImpl, console,
  });
  vm.runInContext(`${source}
    hydrateStateFromUrl = normalizeContentState = applyContentKindMode = writeStateToControls =
      setFiltersExpanded = syncTopicPanelForViewport = bindEvents = loadArchiveFilterPreference =
      loadArchive = () => {};
    globalThis.review = { initialize, initializeGuestBanner, state };
  `, context);
  return { ...context.review, scripts, calls, slot, mount, context,
    resize(value) { width = value; } };
}

const response = payload => async () => ({ ok: true, json: async () => payload });

test("only a successful explicit guest session loads one banner in the calling script's parent", async () => {
  let resolveSession;
  let requests = 0;
  const h = harness(async () => {
    requests++;
    return new Promise(resolve => { resolveSession = resolve; });
  });
  const loading = h.initialize();
  assert.equal(h.scripts.length, 0);
  assert.equal(h.slot.hidden, true);
  resolveSession({ ok: true, json: async () => guest });
  await loading;
  assert.equal(requests, 1);
  assert.equal(h.scripts.length, 1);
  assert.equal(h.scripts[0].src, "https://acscdn.com/script/aclib.js");
  h.scripts[0].onload();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].zoneId, "12327306");
  assert.equal(h.calls[0].parent, h.mount);
  h.initializeGuestBanner(guest);
  await h.initialize();
  assert.equal(h.scripts.length, 1);
  assert.equal(h.calls.length, 1);
});

test("authenticated, admin, unknown and malformed sessions never load advertising", async () => {
  for (const session of [
    { ...guest, authentication: "authenticated", actor: "owner" },
    { ...guest, authentication: "admin", actor: "owner" },
    { ...guest, authentication: "unknown" }, null, {}, { authentication: "guest" },
    { ...guest, actor: "owner" }, { ...guest, capabilities: { rate: true } },
  ]) {
    const h = harness(response(session));
    await h.initialize();
    assert.equal(h.scripts.length, 0, JSON.stringify(session));
    assert.equal(h.slot.hidden, true);
  }
});

test("network, HTTP and JSON failures retain the UI guest fallback without loading ads", async () => {
  for (const fetchImpl of [
    async () => { throw new Error("offline"); },
    async () => ({ ok: false, status: 503, json: async () => ({}) }),
    async () => ({ ok: true, json: async () => { throw new Error("invalid JSON"); } }),
  ]) {
    const h = harness(fetchImpl);
    await h.initialize();
    assert.equal(h.scripts.length, 0);
    assert.equal(h.slot.hidden, true);
  }
});

test("unregistered origins and viewports narrower than the banner never request ads", async () => {
  for (const options of [
    { origin: "http://127.0.0.1:4173" },
    { origin: "https://preview.todaycommunity.pages.dev" },
    { width: 319 },
  ]) {
    const h = harness(response(guest), options);
    await h.initialize();
    assert.equal(h.scripts.length, 0);
    assert.equal(h.slot.hidden, true);
  }
});

test("desktop and mobile select one matching zone and resizing never creates a second placement", async () => {
  for (const [width, zoneId, format] of [
    [320, "12327306", "mobile"], [390, "12327306", "mobile"], [819, "12327306", "mobile"],
    [820, "12331002", "leaderboard"], [1280, "12331002", "leaderboard"],
  ]) {
    const h = harness(response(guest), { width });
    await h.initialize();
    h.scripts[0].onload();
    assert.equal(h.calls.length, 1);
    assert.equal(h.calls[0].zoneId, zoneId);
    assert.equal(h.slot.dataset.format, format);
    h.resize(width >= 820 ? 390 : 1280);
    h.initializeGuestBanner(guest);
    assert.equal(h.scripts.length, 1);
    assert.equal(h.calls.length, 1);
    assert.equal(h.slot.dataset.format, format);
  }
});

test("a delayed wide banner is not initialized after the viewport becomes too narrow", async () => {
  const h = harness(response(guest), { width: 1280 });
  await h.initialize();
  h.resize(390);
  h.scripts[0].onload();
  assert.equal(h.calls.length, 0);
  assert.equal(h.slot.hidden, true);
  h.resize(1280);
  h.initializeGuestBanner(guest);
  assert.equal(h.scripts.length, 1);
  assert.equal(h.calls.length, 0);
});

test("a delayed library cannot mount a banner after the verified session changes", async () => {
  const h = harness(response(guest));
  await h.initialize();
  h.state.feedbackSession = { authentication: "admin" };
  h.scripts[0].onload();
  assert.equal(h.calls.length, 0);
  assert.equal(h.slot.hidden, true);
});

test("blocked, unavailable and throwing ad libraries leave the archive usable without retries", async () => {
  for (const failure of ["network", "missing", "throws"]) {
    const h = harness(response(guest));
    await h.initialize();
    if (failure === "network") h.scripts[0].onerror();
    else {
      if (failure === "missing") h.context.window.aclib = null;
      else h.context.aclib.runBanner = () => { throw new Error("unavailable"); };
      h.scripts[0].onload();
    }
    assert.equal(h.slot.hidden, true);
    h.initializeGuestBanner(guest);
    assert.equal(h.scripts.length, 1);
  }
});

test("only the public archive has a hidden slot and no eager third-party advertising tags", async () => {
  for (const path of ["index.html", "admin/index.html", "owner/index.html", "about.html", "privacy.html"]) {
    const html = await readFile(new URL(`../dashboard/${path}`, import.meta.url), "utf8");
    assert.doesNotMatch(html, /<(?:script|link)[^>]+(?:acscdn|googlesyndication)/u);
    if (path === "index.html") assert.match(html, /id="guest-ad"[^>]*hidden/u);
    else assert.doesNotMatch(html, /id="guest-ad"/u);
  }
  assert.doesNotMatch(source, /runAutoTag|runPop|runInterstitial|adsbygoogle/u);
});
