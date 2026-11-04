import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = (await readFile(new URL("../dashboard/app.js", import.meta.url), "utf8"))
  .replace(/\binitialize\(\);\s*$/, "");

function harness() {
  const nodes = new Map();
  class Element {
    constructor(tag = "div") {
      this.tagName = tag.toUpperCase();
      this.children = [];
      this.attributes = new Map();
      this.dataset = {};
      this.events = {};
      this.style = { setProperty() {} };
      this.classList = { add: value => { this.className = `${this.className || ""} ${value}`; } };
    }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(key, value) { this.attributes.set(key, String(value)); }
    getAttribute(key) { return this.attributes.get(key) ?? null; }
    addEventListener(name, callback) { this.events[name] = callback; }
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
  const window = { location: { href: "https://todaycommunity.pages.dev/?target=ai-practice" } };
  const context = vm.createContext({ document, window, URL, URLSearchParams, console });
  vm.runInContext(`${source}
    globalThis.review = { state, elements, createTitleCell, createAiPracticeSummary,
      createFeedbackCell, applyContentKindMode, normalizeContentState, getLocalViewModel,
      renderSubjectOptions, renderPosts, aiPublicationDate, hydrateStateFromUrl,
      getAvailableArchives, getArticleSourceLabel };
  `, context);
  const api = context.review;
  Object.assign(api.state, { target: "ai-practice", dataSource: "live" });
  api.state.archive = { posts: [], sources: [], summary: {}, pagination: {} };
  return { ...api, document, window, nodes };
}

const sample = {
  post_id: 7, archive_key: "ai-practice", source_key: "ai-practice-hermes",
  title: "에이전트에게 주간 계획을 맡긴 경험", original_title: "Operating a weekly assistant",
  summary: "주간 계획을 맡긴 구성과 실패 뒤 복구 과정을 소개한다.",
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
  assert.match(textOf(panel), /처음 수집/);
  assert.match(textOf(panel), /본문 확인/);
  assert.doesNotMatch(textOf(title), /\[0\]/);
});

test("stored summary and original title are literal text; introduction and unknown publication stay explicit", () => {
  const h = harness();
  const post = { ...sample, summary: '<img src=x onerror="alert(1)">', original_title: "<script>untrusted</script>",
    evidence_level: "introduction", content_kind: "reference", created_at_basis: "first_seen" };
  const panel = h.createAiPracticeSummary(post, "ai-summary-1");
  assert.equal(panel.children[0].children[0].textContent, post.summary);
  assert.match(textOf(panel), /<script>untrusted<\/script>/);
  assert.match(textOf(panel), /소개 내용만 확인 · 원문 본문 미확인/);
  assert.match(textOf(panel), /소개 원제/);
  assert.match(textOf(panel), /참고자료/);
  assert.match(textOf(panel), /발행일 미확인/);
  assert.equal(h.aiPublicationDate(post, true), "미확인");
  assert.equal(h.aiPublicationDate(sample), "2026-11-04");
  assert.equal(h.aiPublicationDate({ ...sample, created_at_raw: "November 4, 2026" }), "2026-11-04");
  assert.equal(h.aiPublicationDate(sample, true), "26.11.04");
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
