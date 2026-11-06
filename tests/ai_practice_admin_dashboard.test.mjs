import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const [script, html, css] = await Promise.all([
  readFile(new URL("../dashboard/admin/admin.js", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/admin/index.html", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/admin/styles.css", import.meta.url), "utf8"),
]);
const privateContent = "PRIVATE-AI-RULES\n\n원문에 없는 성과는 만들지 않는다. h_<hex10> <script>bad()</script>";
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
function payload(content = privateContent, revision = 1, changed = true) {
  return { document: { content, revision, hash: "a".repeat(64), updated_at: "2026-11-07T00:00:00Z", max_length: 6000 },
    application: { total_count: 12, manifest_pending_count: 300, pending_count: 7, stale_count: 9, reassessment_count: 6,
      failed_count: 2, oversized_count: 1, review_count: 1,
      sources: [{ source_key: "ai-practice-hermes", name: "Hermes", total_count: 12, manifest_pending_count: 300, pending_count: 7, failed_count: 2,
        oversized_count: 1, review_count: 1, last_status: "partial", last_attempt_at: "2026-11-07T00:00:00Z",
        backfill: { status: "partial", complete: false, discovered_count: 12,
          cursor_json: '<img src=x onerror="bad()">', scope_json: '{"kind":"archive"}', last_error: "<script>bad()</script>" } }] },
    save: { changed, revision } };
}

function harness() {
  const nodes = new Map();
  class Element {
    constructor(tag = "div") { this.tagName = tag.toUpperCase(); this.children = []; this.value = "";
      this.textContent = ""; this.hidden = false; this.disabled = false; this.dataset = {}; this.events = {}; }
    addEventListener(name, callback) { this.events[name] = callback; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    querySelector() { return new Element("button"); }
    focus() {} select() {}
    set innerHTML(_) { throw new Error("Admin data must not be parsed as HTML"); }
  }
  const document = { createElement: tag => new Element(tag), querySelector(selector) {
    if (!nodes.has(selector)) nodes.set(selector, new Element());
    return nodes.get(selector);
  } };
  const calls = [];
  let handler = async () => response(payload());
  let eventNumber = 0;
  const context = vm.createContext({ document, window: { document }, console, Response,
    crypto: { randomUUID: () => `test-uuid-${String(++eventNumber).padStart(16, "0")}` },
    fetch: async (path, options) => { calls.push({ path, options }); return handler(path, options); } });
  vm.runInContext(script.replace(/void initialize\(\);\s*$/, "")
    + "\nglobalThis.adminTest = { elements, aiRules, showManagement, showLogin, loadAiRules, saveAiRules, initialize };", context);
  return { ...context.adminTest, calls, setFetch: callback => { handler = callback; } };
}
function edit(h, value) { h.elements.aiContent.value = value; h.elements.aiContent.events.input(); }
function textOf(node) { return [node.textContent, ...node.children.map(textOf)].join(" "); }

test("admin can load, edit, save, reload and re-edit one complete AI policy without claiming full application", async () => {
  const h = harness();
  await h.loadAiRules(true);
  assert.equal(h.calls.length, 0);
  h.showManagement();
  await h.loadAiRules(true);
  assert.equal(h.elements.aiContent.value, privateContent);
  assert.equal(h.aiRules.baseRevision, 1);
  assert.match(h.elements.aiApplication.textContent, /현재 기준 미적용 9개/);
  assert.match(h.elements.aiApplication.textContent, /기준 재평가 6개/);
  assert.match(h.elements.aiApplication.textContent, /등록된 후보 12개 \(중복 제외\).*등록 전 발견 항목 300개 \(중복 가능\)/);
  assert.match(textOf(h.elements.aiSources), /등록 후보 12 \(중복 제외\).*등록 전 발견 300 \(중복 가능\)/);
  edit(h, "  개정 기준\r\n\r\n검증 근거를 확인한다.  ");
  h.setFetch(async (path, options) => {
    assert.equal(path, "/api/ai-practice/rules");
    assert.equal(options.cache, "no-store");
    assert.equal(options.credentials, "same-origin");
    assert.equal(options.headers["X-TodayCommunity-Write"], "1");
    const body = JSON.parse(options.body);
    assert.equal(body.base_revision, 1);
    assert.equal(body.content, "개정 기준\n\n검증 근거를 확인한다.");
    return response(payload(body.content, 2));
  });
  await h.saveAiRules();
  assert.equal(h.aiRules.baseRevision, 2);
  assert.match(h.elements.aiStatus.textContent, /다음 수집부터.*순차 재평가/);
  assert.doesNotMatch(h.elements.aiStatus.textContent, /전체.*완료/);
  edit(h, "아직 저장하지 않은 내용");
  h.setFetch(async () => response(payload("서버 저장본", 2)));
  await h.loadAiRules(true);
  assert.equal(h.elements.aiContent.value, "서버 저장본");
  edit(h, "저장본을 다시 편집");
  assert.equal(h.elements.aiContent.value, "저장본을 다시 편집");
  assert.equal(h.elements.aiSave.disabled, false);
});

test("an uncertain save retry reuses its event ID while preserving the draft", async () => {
  const h = harness(); h.showManagement(); await h.loadAiRules(true);
  edit(h, "네트워크 재시도 기준");
  h.setFetch(async () => { throw new TypeError("연결이 끊겼습니다."); });
  await h.saveAiRules();
  assert.equal(h.elements.aiContent.value, "네트워크 재시도 기준");
  assert.match(h.elements.aiStatus.textContent, /편집 내용은 유지/);
  edit(h, "  네트워크 재시도 기준  ");
  h.setFetch(async (_, options) => response(payload(JSON.parse(options.body).content, 2)));
  await h.saveAiRules();
  const attempts = h.calls.filter(call => call.options.method === "POST").map(call => JSON.parse(call.options.body));
  assert.equal(attempts.length, 2);
  assert.equal(attempts[0].event_id, attempts[1].event_id);
  assert.equal(h.aiRules.attempt, null);
});

test("a conflict and status refresh preserve the draft and its original base version until explicit reload", async () => {
  const h = harness(); h.showManagement(); await h.loadAiRules(true);
  edit(h, "내가 편집 중인 기준");
  h.setFetch(async () => response({ error: "다른 곳에서 기준이 바뀌었습니다." }, 409));
  await h.saveAiRules();
  assert.equal(h.elements.aiContent.value, "내가 편집 중인 기준");
  assert.equal(h.aiRules.baseRevision, 1);
  h.setFetch(async () => response(payload("다른 기기의 저장본", 2)));
  await h.loadAiRules(false);
  assert.equal(h.elements.aiContent.value, "내가 편집 중인 기준");
  assert.equal(h.aiRules.baseRevision, 1);
  assert.match(h.elements.aiStatus.textContent, /새 저장 버전/);
  await h.loadAiRules(true);
  assert.equal(h.elements.aiContent.value, "다른 기기의 저장본");
  assert.equal(h.aiRules.baseRevision, 2);
});

test("logout and expired sessions clear private policy and ignore late responses", async () => {
  const h = harness(); h.showManagement(); await h.loadAiRules(true);
  let release;
  h.setFetch(() => new Promise(resolve => { release = resolve; }));
  const loading = h.loadAiRules(false);
  h.showLogin();
  release(response(payload("late PRIVATE response", 3)));
  await loading;
  assert.equal(h.elements.aiContent.value, "");
  assert.equal(h.elements.aiMeta.children.length, 0);
  assert.equal(h.elements.aiSources.children.length, 0);
  assert.equal(h.elements.managementPanel.hidden, true);
  assert.equal(h.aiRules.document, null);
  h.showManagement();
  h.setFetch(async () => response(payload()));
  await h.loadAiRules(true);
  h.setFetch(async () => response({ error: "관리자 로그인이 필요합니다." }, 401));
  await h.saveAiRules();
  assert.equal(h.elements.aiContent.value, "");
  assert.equal(h.aiRules.active, false);
});

test("secret-link sessions never fetch AI settings and content/diagnostics remain inert text", async () => {
  const h = harness();
  h.setFetch(async () => response({ state: "authenticated", capabilities: { manage_rules: true } }));
  await h.initialize();
  assert.deepEqual(h.calls.map(call => call.path), ["/api/auth/session"]);
  assert.equal(h.elements.managementPanel.hidden, true);
  h.showManagement(); h.setFetch(async () => response(payload())); await h.loadAiRules(true);
  assert.equal(h.elements.aiContent.value, privateContent);
  assert.match(textOf(h.elements.aiSources), /<script>bad\(\)<\/script>/);
  assert.match(textOf(h.elements.aiSources), /<img src=x onerror="bad\(\)">/);
  const walk = node => [node, ...node.children.flatMap(walk)];
  assert.ok(walk(h.elements.aiSources).every(node => !["SCRIPT", "IMG"].includes(node.tagName)));
});

test("same-content save explains no requeue and blank/overlong input is rejected before sending", async () => {
  const h = harness(); h.showManagement(); await h.loadAiRules(true);
  for (const invalid of [" \n ", "가".repeat(6001)]) {
    edit(h, invalid); const count = h.calls.length; await h.saveAiRules();
    assert.equal(h.calls.length, count);
    assert.match(h.elements.aiStatus.textContent, /1–6,000자/);
  }
  edit(h, privateContent);
  h.setFetch(async () => response(payload(privateContent, 1, false)));
  await h.saveAiRules();
  assert.match(h.elements.aiStatus.textContent, /재평가를 다시 시작하지 않습니다/);
});

test("admin editor is empty in shipped HTML and supplies native controls and mobile layout", () => {
  assert.match(html, /<textarea[^>]+id="ai-rules-content"[^>]+maxlength="6000"[^>]+aria-describedby="[^"]+"[^>]*><\/textarea>/);
  assert.match(html, /<label for="ai-rules-content">선정기준 전문<\/label>/);
  assert.match(html, /id="ai-rules-status"[^>]+role="status"[^>]+aria-live="polite"/);
  assert.match(html, /id="ai-rules-save" type="submit"/);
  assert.match(html, /기존 포함·제외·검토 글은 순차 재평가/);
  assert.doesNotMatch(script, /localStorage|sessionStorage|innerHTML|selection_policy\.md/);
  assert.match(css, /\.ai-source-list\s*\{\s*grid-template-columns:\s*1fr/);
  assert.match(css, /textarea:focus/);
});
