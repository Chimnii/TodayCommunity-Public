import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../dashboard/analytics.js", import.meta.url), "utf8");
const guest = { authentication: "guest", actor: null, capabilities: { rate: false, manage_auth: false } };

function browser({ saved = null, hash = "", origin = "https://todaycommunity.pages.dev",
  webdriver = false, storageFails = false, standalone = false, track = true,
  sessionResponse = guest } = {}) {
  const scripts = [];
  const events = {};
  const checkbox = { checked: false, disabled: true, addEventListener: (name, fn) => { events[name] = fn; } };
  const message = { textContent: "" };
  const root = { dataset: {} };
  let value = saved;
  let reloads = 0;
  let fetches = 0;
  const context = vm.createContext({
    window: {
      location: { origin, hash, reload: () => { reloads++; } },
      addEventListener: (name, fn) => { events[name] = fn; },
    },
    navigator: { webdriver },
    document: {
      currentScript: { dataset: { track: String(track) }, hasAttribute: name => name === "data-standalone" && standalone },
      documentElement: root,
      querySelector: selector => selector === "#analytics-exclude" ? checkbox : message,
      createElement: () => ({ dataset: {} }),
      head: { append: script => scripts.push(script) },
    },
    localStorage: {
      getItem: () => { if (storageFails) throw new Error("denied"); return value; },
      setItem: (_key, next) => { if (storageFails) throw new Error("denied"); value = next; },
    },
    fetch: async () => {
      fetches++;
      if (sessionResponse instanceof Error) throw sessionResponse;
      return { ok: sessionResponse !== null, json: async () => sessionResponse };
    },
  });
  vm.runInContext(source, context);
  return {
    scripts, checkbox, message, root, events,
    setSession: context.window.tcAnalytics.setSession,
    saved: () => value, fetches: () => fetches, reloads: () => reloads,
    setSaved: next => { value = next; },
  };
}

test("public guest loads the existing beacon once only after session verification", () => {
  const b = browser();
  assert.equal(b.scripts.length, 0);
  b.setSession(guest);
  b.setSession(guest);
  assert.equal(b.scripts.length, 1);
  assert.equal(b.scripts[0].src, "https://static.cloudflareinsights.com/beacon.min.js");
  assert.match(JSON.parse(b.scripts[0].dataset.cfBeacon).token, /^[a-f0-9]{32}$/);
  assert.equal(b.fetches(), 0, "main page reuses the existing session request");
});

test("owner and admin opt out persistently, including after a guest fallback or logout", () => {
  for (const authentication of ["authenticated", "admin"]) {
    const b = browser();
    b.setSession({ authentication });
    b.setSession(guest);
    assert.equal(b.saved(), "1");
    assert.equal(b.scripts.length, 0);
    assert.equal(b.root.dataset.analytics, "excluded");
  }
});

test("first-visit fragment, saved opt-out, preview, automation and blocked storage never load a beacon", () => {
  for (const options of [
    { hash: "#analytics=off" }, { saved: "1" },
    { origin: "http://localhost:4173" }, { origin: "https://preview.todaycommunity.pages.dev" },
    { webdriver: true }, { storageFails: true },
  ]) {
    const b = browser(options);
    b.setSession(guest);
    assert.equal(b.scripts.length, 0, JSON.stringify(options));
  }
  assert.equal(browser({ hash: "#analytics=off" }).saved(), "1");
});

test("unknown, malformed and failed authentication never count as a verified visitor", () => {
  for (const payload of [null, {}, { authentication: "guest" },
    { ...guest, actor: "owner" }, { ...guest, capabilities: { rate: true, manage_auth: false } }]) {
    const b = browser();
    b.setSession(payload);
    assert.equal(b.scripts.length, 0);
  }
});

test("privacy choice persists without counting the settings page; enabling only affects later pages", () => {
  const b = browser({ track: false });
  b.checkbox.checked = true;
  b.events.change();
  b.setSession(guest);
  assert.equal(b.saved(), "1");
  assert.equal(b.scripts.length, 0);
  b.checkbox.checked = false;
  b.events.change();
  b.setSession(guest);
  assert.equal(b.saved(), "0");
  assert.equal(b.scripts.length, 0);
});

test("another tab opting out unloads a running beacon and prevents a pending one", () => {
  const running = browser();
  running.setSession(guest);
  running.events.storage({ key: "tc-analytics-excluded", newValue: "1" });
  assert.equal(running.reloads(), 1);
  const pending = browser();
  pending.setSaved("1");
  pending.setSession(guest);
  assert.equal(pending.scripts.length, 0);
});

test("standalone pages check the session once and fail closed without affecting rendering", async () => {
  for (const sessionResponse of [guest, { authentication: "admin" }, null, new Error("offline")]) {
    const b = browser({ standalone: true, sessionResponse });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(b.fetches(), 1);
    assert.equal(b.scripts.length, sessionResponse === guest ? 1 : 0);
  }
});
