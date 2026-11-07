import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = (await readFile(new URL("../dashboard/app.js", import.meta.url), "utf8"))
  .replace(/\binitialize\(\);\s*$/, "");

function harness(fetchImpl = async () => { throw new Error("Unexpected request"); }) {
  const nodes = new Map();
  class Element {
    constructor(tag = "div") {
      this.tagName = tag.toUpperCase();
      this.children = [];
      this.attributes = new Map();
      this.dataset = {};
      this.events = {};
      this.open = false;
      this.isConnected = true;
      this.style = { setProperty() {} };
      this.classList = { add: value => { this.className = `${this.className || ""} ${value}`; } };
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this._text = ""; this.children = children; }
    set textContent(value) { this._text = value; this.children = []; }
    get textContent() { return this._text || ""; }
    setAttribute(key, value) { this.attributes.set(key, String(value)); }
    getAttribute(key) { return this.attributes.get(key) ?? null; }
    addEventListener(name, callback) { this.events[name] = callback; }
    focus() { document.activeElement = this; }
    showModal() { this.open = true; }
    close() { this.open = false; this.events.close?.(); }
    get options() { return this.children; }
    set id(value) { this._id = value; nodes.set(value, this); }
    get id() { return this._id; }
  }
  const document = {
    body: new Element("body"),
    createElement: tag => new Element(tag),
    getElementById: id => nodes.get(id),
    querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, new Element());
      return nodes.get(selector);
    },
  };
  const window = { location: { href: "https://todaycommunity.pages.dev/?target=ai-practice" },
    clearTimeout() {}, setTimeout: () => 1 };
  const context = vm.createContext({ document, window, URL, URLSearchParams, AbortController, fetch: fetchImpl, console });
  vm.runInContext(`${source}
    globalThis.review = { state, elements, createTitleCell, createAiPracticeSummary, createSubjectCell,
      createFeedbackCell, applyContentKindMode, normalizeContentState, getLocalViewModel,
      renderSubjectOptions, renderPosts, articlePublicationDate, hydrateStateFromUrl,
      getAvailableArchives, getArticleSourceLabel, bindAiCriteriaEvents, openAiCriteriaDialog,
      closeAiCriteriaDialog, renderPreferenceDocument, savePreferenceDocument, refreshHiddenItems };
  `, context);
  const api = context.review;
  Object.assign(api.state, { target: "ai-practice", dataSource: "live" });
  api.state.archive = { posts: [], sources: [], summary: {}, pagination: {} };
  api.bindAiCriteriaEvents();
  return { ...api, document, window, nodes };
}

const sample = {
  post_id: 7, archive_key: "ai-practice", source_key: "ai-practice-hermes",
  title: "에이전트에게 주간 계획을 맡긴 경험", original_title: "Operating a weekly assistant",
  summary: "에이전트가 주간 계획을 작성한다.\n일정이 겹치면 후보 시간을 제안한다.\n사용자가 승인한 계획을 달력에 저장한다.",
  subject: "생활 비서", publisher: "Hermes", content_kind: "experience", evidence_level: "full_text",
  post_url: "https://hermes-agent.nousresearch.com/docs/user-stories",
  created_at: "2026-11-04T00:00:00Z", created_at_raw: "2026-11-04", created_at_basis: "source",
  created_at_precision: "date", first_seen_at: "2026-11-05T00:12:00Z", upvotes: 0, comments: 0,
};

function textOf(element) {
  return [element.textContent || "", ...element.children.map(textOf)].join(" ");
}

test("AI archive uses the existing subject filter and never exposes game preference controls", () => {
  const h = harness();
  h.state.feedbackSession = { authentication: "authenticated", capabilities: { rate: true } };
  h.applyContentKindMode();
  h.renderSubjectOptions();
  assert.equal(h.document.body.dataset.contentKind, "article");
  assert.equal(h.document.body.dataset.archiveKey, "ai-practice");
  assert.equal(h.elements.gameNewsTools.hidden, true);
  assert.equal(h.elements.subjectFilterLabel.textContent, "말머리");
  assert.equal(h.elements.subjectColumnLabel.textContent, "말머리");
  assert.equal(h.elements.dateColumnLabel.textContent, "발행일");
  assert.deepEqual(h.elements.subjectSelect.options.map(option => option.value).sort(),
    ["", "생활 비서", "프로젝트 운영", "모델 협의"].sort());
  assert.match(h.elements.searchInput.placeholder, /원제/);
  Object.assign(h.state, { minUpvotes: 3, minComments: 5, sortBy: "comments" });
  h.normalizeContentState();
  assert.equal(h.state.minUpvotes, 0);
  assert.equal(h.state.minComments, 0);
  assert.equal(h.state.sortBy, "created_at");
  assert.equal(h.createFeedbackCell({ ...sample, feedback_key: "a".repeat(32) }).children.length, 0);
  h.state.target = "game-news";
  h.applyContentKindMode();
  assert.equal(h.elements.gameNewsTools.hidden, false, "existing game tools remain available");
});

test("AI summary toggle has an independent original link and keyboard-native button with explicit state", () => {
  const h = harness();
  const title = h.createTitleCell(sample, "ai-summary-1");
  const panel = h.createAiPracticeSummary(sample, "ai-summary-1");
  const [link, toggle] = title.children;
  assert.equal(link.tagName, "A");
  assert.equal(link.href, sample.post_url);
  assert.equal(link.target, "_blank");
  assert.match(link.rel, /noopener/);
  assert.equal(toggle.tagName, "BUTTON");
  assert.equal(toggle.type, "button");
  assert.equal(toggle.getAttribute("aria-label"), `${sample.title}, 요약과 원문 정보`);
  assert.equal(toggle.children[0].className, "ai-summary-label");
  assert.equal(toggle.children[0].textContent, "요약");
  assert.equal(toggle.getAttribute("aria-controls"), panel.id);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.equal(panel.hidden, true);
  toggle.events.click();
  assert.equal(panel.hidden, false);
  assert.equal(toggle.getAttribute("aria-expanded"), "true");
  toggle.events.click();
  assert.equal(panel.hidden, true);
  assert.equal(toggle.getAttribute("aria-expanded"), "false");
  assert.match(textOf(panel), /Operating a weekly assistant/);
  assert.equal(panel.children[0].children[0].tagName, "P");
  assert.equal(panel.children[0].children[0].textContent, sample.summary.replace(/\s+/g, " ").trim());
  assert.doesNotMatch(textOf(panel), /처음 수집|본문 확인|확인 범위|자료 유형|말머리|출처/);
  assert.doesNotMatch(textOf(title), /\[0\]/);
});

test("AI mobile subject abbreviations preserve full labels across archive views", () => {
  const h = harness();
  for (const target of ["ai-practice", "all", "favorites"]) {
    h.state.target = target;
    for (const [subject, short] of [["생활 비서", "비서"], ["프로젝트 운영", "플젝"], ["모델 협의", "협의"]]) {
      const cell = h.createSubjectCell({ ...sample, subject });
      assert.equal(cell.getAttribute("aria-label"), subject);
      assert.equal(cell.children[0].textContent, subject);
      assert.equal(cell.children[1].textContent, short);
    }
  }
});

test("stored summary and original title are literal text; introduction and unknown publication stay explicit", () => {
  const h = harness();
  const post = { ...sample, summary: '<img src=x onerror="alert(1)">', original_title: "<script>untrusted</script>",
    evidence_level: "introduction", content_kind: "reference", created_at_basis: "first_seen" };
  const panel = h.createAiPracticeSummary(post, "ai-summary-1");
  assert.equal(panel.children[0].children[0].textContent, post.summary);
  assert.match(textOf(panel), /<script>untrusted<\/script>/);
  assert.match(textOf(panel), /소개 원제/);
  assert.doesNotMatch(textOf(panel), /참고자료|발행일 미확인|처음 수집|확인 범위/);
  assert.equal(h.articlePublicationDate(post), "-");
  assert.equal(h.articlePublicationDate({ ...sample, created_at: "invalid" }), "-");
  assert.equal(h.articlePublicationDate({ ...sample, created_at_raw: "November 4, 2026" }), "26.11.04");
  for (const archive_key of ["ai-practice", "game-news"]) {
    for (const target of [archive_key, "all", "favorites"]) {
      h.state.target = target;
      h.renderPosts([{ ...post, archive_key }]);
      assert.equal(h.elements.posts.children[0].children.find(cell => cell.className.includes("cell-date")).textContent, "-");
    }
  }
  assert.equal(h.getArticleSourceLabel(sample), "Hermes");
});

test("local fallback searches Korean and original titles with the same subject narrowing", () => {
  const h = harness();
  h.state.dataSource = "local";
  h.state.archive.posts = [sample, { ...sample, post_id: 8, title: "모델 간 검토", original_title: "Model review", subject: "모델 협의" }];
  for (const search of ["주간 계획", "WEEKLY ASSISTANT"]) {
    Object.assign(h.state, { search, subject: "생활 비서" });
    assert.deepEqual(Array.from(h.getLocalViewModel().posts, post => post.post_id), [7]);
  }
  h.state.subject = "모델 협의";
  assert.equal(h.getLocalViewModel().posts.length, 0);
});

test("AI source code literals and hostile markup stay text in titles, attributes and metadata", () => {
  const h = harness();
  const post = { ...sample,
    title: 'h_<hex10> " onmouseover="bad() <script>bad()</script>',
    summary: '경로 h_<hex10> & </script><script id="ai-summary-injected">bad()</script>',
    original_title: '<img src=x onerror="bad()"> h_<hex10>',
    publisher: '<svg onload="bad()">', subject: '<b>프로젝트 운영</b>',
  };
  for (const target of ["ai-practice", "all", "favorites"]) {
    h.state.target = target;
    h.renderPosts([post]);
    const [row, panel] = h.elements.posts.children;
    const titleCell = row.children.find(child => child.className.includes("cell-title"));
    const [link, toggle] = titleCell.children;
    assert.equal(link.children[0].textContent, post.title);
    assert.equal(link.title, post.title);
    assert.equal(link.getAttribute("aria-label"), `${post.title}, 원문 열기`);
    assert.equal(toggle.getAttribute("aria-label"), `${post.title}, 요약과 원문 정보`);
    assert.equal(link.getAttribute("onmouseover"), null);
    const [summary, metadata] = panel.children[0].children;
    assert.equal(summary.textContent, post.summary);
    assert.equal(summary.children.length, 0);
    assert.deepEqual(metadata.children.map(node => node.textContent), ["원제", post.original_title]);
    const walk = node => [node, ...node.children.flatMap(walk)];
    assert.equal(walk(h.elements.posts).some(node => ["SCRIPT", "IMG", "SVG", "B"].includes(node.tagName)), false);
  }
});

test("AI summaries survive mixed and favorites rendering while community comments retain their display", () => {
  const h = harness();
  for (const target of ["ai-practice", "all", "favorites"]) {
    h.state.target = target;
    h.renderPosts([sample]);
    assert.equal(h.elements.posts.children.length, 2);
    assert.equal(h.elements.posts.children[1].className, "ai-summary-row");
    assert.equal(h.elements.posts.children[1].hidden, true);
    assert.doesNotMatch(textOf(h.elements.posts.children[0]), /\[0\]/);
  }
  h.state.target = "dcinside-singularity";
  const cell = h.createTitleCell({ title: "커뮤니티 글", comments: 2, post_url: "https://example.org/" });
  assert.match(textOf(cell), /\[2\]/);
  assert.equal(cell.children.length, 1);
});

test("URL restoration keeps the AI target, query, subject and sequential page state", () => {
  const h = harness();
  h.window.location.href = "https://todaycommunity.pages.dev/?target=ai-practice&q=weekly&subject="
    + encodeURIComponent("생활 비서") + "&page=2&cursor=next";
  h.hydrateStateFromUrl();
  assert.equal(h.state.target, "ai-practice");
  assert.equal(h.state.search, "weekly");
  assert.equal(h.state.subject, "생활 비서");
  assert.equal(h.state.page, 2);
  assert.equal(h.state.cursor, "next");
  assert.equal(h.document.title, "AI 실전 사례 모음 | 오늘의 커뮤니티");
});

const criteriaResponse = (content = "선정 범위\n\n- 첫 번째 기준\n- 두 번째 기준", revision = 3) => ({
  ok: true,
  json: async () => ({ document: { content, revision, updated_at: "2026-11-07T16:54:52Z" } }),
});

test("AI criteria read controls allow only authenticated and admin sessions in the AI tab", async () => {
  const h = harness();
  for (const authentication of [undefined, "guest", "authenticated", "admin", "owner"]) {
    h.state.feedbackSession = { authentication, capabilities: { rate: true, manage_rules: true } };
    for (const target of ["ai-practice", "game-news", "all", "favorites", "dcinside-singularity"]) {
      h.state.target = target;
      h.applyContentKindMode();
      const allowed = target === "ai-practice" && ["authenticated", "admin"].includes(authentication);
      assert.equal(h.elements.aiCriteriaTools.hidden, !allowed);
      assert.equal(h.elements.gameNewsTools.hidden, target !== "game-news");
      if (!allowed) {
        await h.openAiCriteriaDialog();
        assert.equal(h.elements.aiCriteriaDialog.open, false);
      }
    }
  }
  h.state.target = "ai-practice";
  h.state.feedbackSession = { authentication: "authenticated", capabilities: {} };
  h.applyContentKindMode();
  assert.equal(h.elements.aiCriteriaTools.hidden, false, "read access is independent of game write capabilities");
});

test("AI criteria load fresh on every opening and render exact literal text with revision and date", async () => {
  const requests = [];
  const content = '소제목\n\n- <script>alert(1)</script>\n- <img src=x onerror="bad()"> & h_<hex10>';
  const h = harness(async (url, options) => {
    requests.push({ url, options });
    return criteriaResponse(requests.length === 1 ? content : "새로 저장된 전문", requests.length + 2);
  });
  h.state.feedbackSession = { authentication: "admin" };
  h.applyContentKindMode();
  const loading = h.openAiCriteriaDialog();
  assert.equal(h.elements.aiCriteriaDialog.open, true);
  assert.match(h.elements.aiCriteriaStatus.textContent, /불러오는 중/);
  assert.equal(h.elements.aiCriteriaDocument.getAttribute("aria-busy"), "true");
  assert.equal(h.document.activeElement, h.elements.aiCriteriaClose);
  await loading;
  assert.equal(h.elements.aiCriteriaDocument.textContent, content);
  assert.equal(h.elements.aiCriteriaDocument.children.length, 0, "source markup is never parsed");
  assert.match(h.elements.aiCriteriaMeta.textContent, /2026.*버전 3/);
  assert.equal(h.elements.aiCriteriaDocument.getAttribute("aria-busy"), "false");
  assert.equal(h.elements.aiCriteriaOpen.getAttribute("aria-expanded"), "true");
  h.elements.aiCriteriaClose.events.click();
  assert.equal(h.elements.aiCriteriaDocument.textContent, "");
  assert.equal(h.elements.aiCriteriaMeta.textContent, "");
  assert.equal(h.elements.aiCriteriaOpen.getAttribute("aria-expanded"), "false");
  assert.equal(h.document.activeElement, h.elements.aiCriteriaOpen);
  await h.openAiCriteriaDialog();
  assert.equal(h.elements.aiCriteriaDocument.textContent, "새로 저장된 전문");
  assert.match(h.elements.aiCriteriaMeta.textContent, /버전 4/);
  assert.equal(requests.length, 2);
  for (const { url, options } of requests) {
    assert.equal(url, "/api/ai-practice/criteria");
    assert.equal(options.cache, "no-store");
    assert.equal(options.method, undefined, "read view never sends a write");
    assert.ok(options.signal instanceof AbortSignal);
  }
  let prevented = false;
  h.elements.aiCriteriaDialog.events.cancel({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(h.elements.aiCriteriaDialog.open, false);
  assert.equal(h.document.activeElement, h.elements.aiCriteriaOpen);
  assert.equal(h.elements.aiCriteriaDocument.textContent, "");
});

test("closing cancels AI criteria and a late prior response cannot overwrite a reopened dialog", async () => {
  const pending = [];
  const h = harness((url, options) => new Promise(resolve => pending.push({ resolve, options })));
  h.state.feedbackSession = { authentication: "authenticated" };
  h.applyContentKindMode();
  const first = h.openAiCriteriaDialog();
  h.closeAiCriteriaDialog();
  assert.equal(pending[0].options.signal.aborted, true);
  const second = h.openAiCriteriaDialog();
  h.elements.aiCriteriaDialog.events.close(); // delayed native close event from the previous opening
  assert.equal(pending[1].options.signal.aborted, false);
  pending[1].resolve(criteriaResponse("현재 전문", 4));
  await second;
  pending[0].resolve(criteriaResponse("늦게 도착한 이전 전문", 3));
  await first;
  assert.equal(h.elements.aiCriteriaDocument.textContent, "현재 전문");
  h.elements.aiCriteriaDialog.events.click({ target: h.elements.aiCriteriaDialog });
  assert.equal(h.elements.aiCriteriaDialog.open, false);
  assert.equal(h.elements.aiCriteriaDocument.textContent, "");
});

test("AI criteria preserve all 6000 characters, paragraph breaks and list lines", async () => {
  const content = "선정 기준\n\n- ".padEnd(6000, "가");
  const h = harness(async () => criteriaResponse(content));
  h.state.feedbackSession = { authentication: "authenticated" };
  h.applyContentKindMode();
  await h.openAiCriteriaDialog();
  assert.equal(h.elements.aiCriteriaDocument.textContent, content);
  assert.equal(h.elements.aiCriteriaDocument.textContent.length, 6000);
  assert.equal(h.elements.aiCriteriaStatus.textContent, "");
});

test("AI criteria clear on access loss, actor change or tab navigation and ignore in-flight responses", async () => {
  for (const change of [
    h => { h.state.feedbackSession = { authentication: "guest" }; },
    h => { h.state.feedbackSession = { authentication: "authenticated", actor: "another" }; },
    h => { h.state.target = "game-news"; },
  ]) {
    let finish;
    const h = harness(() => new Promise(resolve => { finish = resolve; }));
    h.state.feedbackSession = { authentication: "authenticated", actor: "original" };
    h.applyContentKindMode();
    const loading = h.openAiCriteriaDialog();
    change(h);
    h.applyContentKindMode();
    finish(criteriaResponse("이전 사용자에게만 허용된 내용"));
    await loading;
    assert.equal(h.elements.aiCriteriaDialog.open, false);
    assert.equal(h.elements.aiCriteriaDocument.textContent, "");
    assert.equal(h.elements.aiCriteriaMeta.textContent, "");
  }
});

test("AI criteria failures and malformed responses expose no stale content and allow a fresh retry", async () => {
  for (const response of [
    { ok: false, status: 500, json: async () => ({ error: "unavailable" }) },
    { ok: true, json: async () => ({ document: { content: "x".repeat(6001), revision: 3, updated_at: "2026-11-07" } }) },
    { ok: true, json: async () => ({ document: { content: "bad revision", revision: "3", updated_at: "2026-11-07" } }) },
  ]) {
    let count = 0;
    const h = harness(async () => ++count === 1 ? response : criteriaResponse());
    h.state.feedbackSession = { authentication: "admin" };
    h.applyContentKindMode();
    await h.openAiCriteriaDialog();
    assert.match(h.elements.aiCriteriaStatus.textContent, /불러오지 못했습니다/);
    assert.equal(h.elements.aiCriteriaDocument.textContent, "");
    assert.equal(h.elements.aiCriteriaMeta.textContent, "");
    h.closeAiCriteriaDialog();
    await h.openAiCriteriaDialog();
    assert.match(h.elements.aiCriteriaDocument.textContent, /첫 번째 기준/);
  }
  for (const status of [401, 403]) {
    const h = harness(async () => ({ ok: false, status, json: async () => ({ error: "denied" }) }));
    h.state.feedbackSession = { authentication: "admin" };
    h.applyContentKindMode();
    await h.openAiCriteriaDialog();
    assert.equal(h.state.feedbackSession.authentication, "guest");
    assert.equal(h.elements.aiCriteriaTools.hidden, true);
    assert.equal(h.elements.aiCriteriaDialog.open, false);
    assert.equal(h.elements.aiCriteriaDocument.textContent, "");
  }
});

test("AI read dialog keeps game preference saving and hidden-list restoration controls intact", async () => {
  const requests = [];
  const h = harness(async (url, options) => {
    requests.push({ url, options });
    if (url === "/api/game-news/preferences") return { ok: true, json: async () => ({ document: {
      content: "게임 전용 선호", version: 5, updated_at: "2026-11-07T10:00:00Z", max_length: 1000,
    } }) };
    return { ok: true, json: async () => ({ items: [{ post_key: "a".repeat(32), title: "숨긴 게임 기사", post_url: "https://example.com/game", subject: "게임" }] }) };
  });
  h.state.target = "game-news";
  h.state.feedbackSession = { authentication: "authenticated", capabilities: { rate: true, hide: true, manage_rules: true } };
  h.applyContentKindMode();
  h.renderPreferenceDocument({ content: "이전 선호", version: 4, updated_at: "2026-11-07T09:00:00Z" });
  h.elements.preferenceDocument.value = "게임 전용 선호";
  await h.savePreferenceDocument();
  assert.equal(requests[0].url, "/api/game-news/preferences");
  assert.equal(requests[0].options.method, "POST");
  assert.equal(JSON.parse(requests[0].options.body).base_version, 4);
  assert.equal(JSON.parse(requests[0].options.body).content, "게임 전용 선호");
  assert.equal(h.state.preferenceDocument.version, 5);
  await h.refreshHiddenItems();
  assert.equal(requests[1].url, "/api/game-news/hidden");
  assert.equal(h.elements.hiddenList.children[0].children[1].textContent, "복구");
  assert.equal(h.elements.gameNewsTools.hidden, false);
  assert.equal(h.elements.aiCriteriaTools.hidden, true);
});

test("AI criteria modal ships empty, read-only and keyboard scrollable with mobile wrapping", async () => {
  const html = await readFile(new URL("../dashboard/index.html", import.meta.url), "utf8");
  const css = await readFile(new URL("../dashboard/styles.css", import.meta.url), "utf8");
  const dialog = /<dialog[^>]*id="ai-criteria-dialog"[\s\S]*?<\/dialog>/.exec(html)?.[0];
  assert.ok(dialog);
  assert.match(dialog, /aria-labelledby="ai-criteria-title"/);
  assert.match(dialog, /id="ai-criteria-close"[^>]*aria-label="선정 기준 닫기"[^>]*autofocus/);
  assert.match(dialog, /id="ai-criteria-document"[^>]*role="region"[^>]*tabindex="0"><\/div>/);
  assert.doesNotMatch(dialog, /textarea|<form|type="submit"|저장<|편집</);
  assert.match(css, /\.ai-criteria-document\s*\{[^}]*white-space:\s*pre-wrap[^}]*overflow-wrap:\s*anywhere/s);
  assert.match(css, /\.ai-criteria-dialog\s*\{[^}]*100dvh/s);
  assert.match(css, /\.management-dialog-content\s*\{[^}]*overflow:\s*auto/s);
});
