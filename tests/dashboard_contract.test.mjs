import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function readOptionalFile(url) {
  try {
    return await readFile(url, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

const [html, app, css, design, fixtureServer] = await Promise.all([
  readFile(new URL("../dashboard/index.html", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/app.js", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/styles.css", import.meta.url), "utf8"),
  readOptionalFile(new URL("../DESIGN.md", import.meta.url)),
  readFile(new URL("./dashboard_fixture_server.mjs", import.meta.url), "utf8"),
]);

test("ships the compact archive surface and hidden collection dialog", () => {
  assert.match(html, /<title>오늘의 커뮤니티 \| 선별 글 아카이브<\/title>/);
  assert.match(html, /id="archive-board"[\s\S]*role="table"/);
  assert.match(html, /id="pagination"[\s\S]*aria-label="게시글 페이지"/);
  assert.match(html, /<dialog[^>]*id="runs-drawer"/);
  assert.doesNotMatch(html, /<dialog[^>]*\sopen(?:\s|>)/);
  assert.match(html, /id="result-count"[^>]*aria-live="polite"/);
  assert.match(html, /id="search-input"[^>]*type="search"/);
  assert.match(html, /id="subject-select"[^>]*name="subject"/);
  assert.match(html, /<option value="">전체 말머리<\/option>/);
  assert.match(html, /id="upvotes-input"[^>]*type="number"/);
  assert.match(html, /id="comments-input"[^>]*type="number"/);
  assert.match(
    html,
    /cell-number"[^>]*role="columnheader"[^>]*>번호<\/span>[\s\S]*cell-subject"[\s\S]*role="columnheader"[\s\S]*>말머리<\/span>[\s\S]*cell-source"[^>]*role="columnheader"[^>]*>출처<\/span>[\s\S]*cell-title"[^>]*role="columnheader"[^>]*>제목<\/span>/
  );
  assert.match(
    html,
    /cell-title"[^>]*role="columnheader"[^>]*>제목<\/span>[\s\S]*cell-upvotes"[^>]*role="columnheader"[^>]*>추천<\/span>[\s\S]*cell-date"[^>]*role="columnheader"[^>]*>작성일<\/span>/
  );
  assert.doesNotMatch(html, /cell-comments" role="columnheader"/);
  assert.match(
    html,
    /<h1><a class="masthead-home" href="\/">오늘의 커뮤니티<\/a><\/h1>/
  );
});

test("ships six accessible archive tabs and replaces them from the API catalog", () => {
  assert.match(html, /id="archive-tabs"[^>]*role="tablist"/);
  assert.match(html, /role="tab"[\s\S]*href="\/?\?target=dcinside-singularity"/);
  assert.match(html, /href="\/?\?target=dcinside-agent-stack"/);
  assert.match(html, /href="\/?\?target=dcinside-zeus-pride"/);
  assert.match(html, /href="\/?\?target=fmkorea-munich"/);
  assert.match(html, /href="\/?\?target=game-news"/);
  assert.match(html, /href="\/?\?target=all"/);
  assert.match(html, />특이점이 온다 갤<\/a>/);
  assert.match(html, />AI 활용 갤<\/a>/);
  assert.match(html, />제우스 오만의 신 갤<\/a>/);
  assert.match(html, />Bayern Munich<\/a>/);
  assert.match(html, />게임 뉴스<\/a>/);
  assert.match(html, />모두<\/a>/);

  assert.match(app, /Array\.isArray\(state\.archive\?\.archives\)/);
  assert.match(app, /getAvailableArchives\(\)\.map/);
  assert.match(app, /tab\.setAttribute\("role", "tab"\)/);
  assert.match(app, /tab\.setAttribute\("aria-selected", String\(key === state\.target\)\)/);
  assert.match(app, /keepSelectedArchiveTabVisible\(\)/);
  assert.match(app, /navigation\.scrollLeft = selectedEnd - navigation\.clientWidth/);
  assert.match(app, /\["ArrowLeft", "ArrowRight", "Home", "End"\]/);
  assert.match(app, /"dcinside-singularity": "특이점이 온다 갤"/);
  assert.match(app, /"dcinside-agent-stack": "AI 활용 갤"/);
  assert.match(app, /"dcinside-zeus-pride": "제우스 오만의 신 갤"/);
  assert.match(app, /"fmkorea-munich": "Bayern Munich"/);
  assert.match(app, /"game-news": "게임 뉴스"/);
  assert.match(app, /all: "모두"/);
  assert.match(
    app,
    /archive_key: "dcinside-agent-stack",[\s\S]*display_name: "AI 활용",[\s\S]*description: "디시인사이드 AI 활용 갤러리 인기글"/
  );
  assert.match(
    app,
    /archive_key: "dcinside-zeus-pride",[\s\S]*display_name: "제우스 오만의 신",[\s\S]*description: "디시인사이드 제우스 오만의 신 갤러리 인기글"/
  );
  assert.match(app, /ARCHIVE_TAB_LABELS\[key\] \|\| String\(archive\.display_name \|\| key\)/);
  assert.match(css, /\.archive-tab\[aria-selected="true"\]/);
});

test("renders the all target as a mixed five-column archive without feedback", () => {
  assert.match(app, /const ALL_TARGET = "all"/);
  assert.match(app, /archive_key: ALL_TARGET,[\s\S]*content_kind: "mixed"/);
  assert.match(app, /function isMixedArchive\(\)/);
  assert.match(app, /document\.body\.dataset\.contentKind = mixedMode/);
  assert.match(app, /mixedMode \? "소속" : "출처"/);
  assert.match(app, /mixedMode[\s\S]*"분류"/);
  assert.match(app, /"dcinside-agent-stack": "AI활용"/);
  assert.match(app, /"dcinside-zeus-pride": "제우스"/);
  assert.match(app, /"game-news": "게임뉴스"/);
  assert.match(app, /elements\.numberColumnLabel,[\s\S]*elements\.sourceColumnLabel,[\s\S]*elements\.subjectColumnLabel/);
  assert.match(app, /isMixedArchive\(\) && isGameNewsPost\(post\)[\s\S]*\? "-"/);
  assert.match(app, /cell\.textContent = `\$\{sourceLabel\}-\$\{subjectLabel\}`/);
  assert.match(app, /ARCHIVE_ROW_LABELS\[post\?\.archive_key\]/);
  assert.match(app, /const communityArchive = !isArticleArchive\(\) && !isMixedArchive\(\)/);
  assert.match(css, /body\[data-content-kind="mixed"\] \.board-row\s*{[^}]*grid-template-columns:\s*60px 84px minmax\(0, 1fr\) 64px 104px/s);
  assert.match(css, /body\[data-content-kind="mixed"\] \.cell-number,[\s\S]*body\[data-content-kind="mixed"\] \.cell-feedback\s*{[^}]*display:\s*none/s);
  assert.match(css, /body\[data-content-kind="mixed"\] \.cell-source\s*{[^}]*display:\s*block/s);
  assert.match(
    css,
    /@media \(max-width:\s*520px\)[\s\S]*body\[data-content-kind="mixed"\] \.post-row\s*{[^}]*grid-template-columns:\s*56px minmax\(0, 1fr\) 38px/
  );
  assert.match(
    css,
    /@media \(max-width:\s*520px\)[\s\S]*body\[data-content-kind="mixed"\] \.cell-subject\s*{[^}]*display:\s*none/
  );
  assert.doesNotMatch(css, /body\[data-content-kind="mixed"\] \.cell-date\s*{[^}]*display:\s*block/s);
});

test("offers link-specific archive checkboxes only inside the all-tab filter form", () => {
  assert.match(
    html,
    /<form class="filter-form" id="filter-form">[\s\S]*id="filter-reset"[\s\S]*<fieldset class="archive-filter" id="archive-filter" hidden>[\s\S]*<legend>표시할 탭<\/legend>[\s\S]*id="archive-filter-options"[\s\S]*<\/fieldset>[\s\S]*<\/form>/
  );
  assert.match(app, /state\.feedbackSession\?\.authentication === "authenticated"/);
  assert.match(
    app,
    /state\.target === ALL_TARGET &&[\s\S]*hasSecretLinkSession\(\) &&[\s\S]*state\.archiveFilterLoaded/
  );
  assert.match(app, /fetchGameNewsJson\("\/api\/auth\/archive-filters"\)/);
  assert.match(app, /postAuthJson\("\/api\/auth\/archive-filters"/);
  assert.match(app, /"X-TodayCommunity-Auth": "1"/);
  assert.match(app, /params\.append\("exclude_archive", archiveKey\)/);
  assert.match(app, /state\.excludedArchiveKeys = new Set\(\)/);
  assert.match(app, /queueArchiveFilterPreferenceSave\(\)/);
  // The shared public-cache expiry may persist; link-specific filter choices
  // must continue using their authenticated server endpoint.
  const storageKeys = Array.from(
    app.matchAll(/localStorage\.(?:getItem|setItem)\("([^"]+)"/g),
    (match) => match[1]
  );
  assert.deepEqual(storageKeys, ["tc-archive-refresh-until", "tc-archive-refresh-until"]);
  assert.doesNotMatch(app, /sessionStorage/);
  assert.match(css, /\.archive-filter\s*{[^}]*grid-column:\s*1 \/ -1/s);
  assert.match(css, /\.archive-filter\[hidden\]\s*{[^}]*display:\s*none/s);
  assert.match(
    css,
    /\.filter-form\s*{[^}]*display:\s*none[\s\S]*\.filter-shell\.is-filter-expanded \.filter-form\s*{[^}]*display:\s*grid/
  );
  assert.doesNotMatch(app, /이 시크릿 링크에 자동 저장됩니다/);
  assert.match(fixtureServer, /searchParams\.getAll\("exclude_archive"\)/);
  assert.match(fixtureServer, /requestUrl\.pathname === "\/api\/auth\/archive-filters"/);
});

test("ships a stored hot-topic rail with URL-backed filtering and a compact accordion", () => {
  assert.match(html, /<aside[^>]*id="topic-panel"[^>]*aria-labelledby="topic-panel-title"[^>]*hidden/);
  assert.match(html, /id="topic-panel-title"[^>]*tabindex="-1"[^>]*>TOPICS/);
  assert.doesNotMatch(html, /topic-panel-eyebrow|topic-summary/);
  assert.match(
    html,
    /id="topic-panel-toggle"[\s\S]*aria-controls="topic-panel-content"[\s\S]*aria-expanded="true"/
  );
  assert.match(app, /topicId:\s*0/);
  assert.match(app, /params\.set\("topic", String\(state\.topicId\)\)/);
  assert.match(app, /state\.topicId = normalizePositiveNumber\(params\.get\("topic"\), 0\)/);
  assert.match(app, /topic:\s*state\.topicId \|\| null/);
  assert.match(app, /const trends = state\.archive\?\.topic_trends/);
  assert.match(app, /const selectedTopic = state\.archive\?\.selected_topic/);
  assert.match(app, /button\.setAttribute\("aria-pressed", String\(topicId === state\.topicId\)\)/);
  assert.match(app, /`\$\{label\}, \$\{numberFormatter\.format\(count\)\}개 글`/);
  assert.match(app, /countElement\.textContent = `\(\$\{numberFormatter\.format\(count\)\}개\)`/);
  assert.match(app, /button\.append\(labelElement, countElement\)/);
  assert.match(app, /applyTopicFilter\(topicId === state\.topicId \? 0 : topicId\)/);
  assert.match(app, /elements\.topicPanel\.hidden = !communityArchive/);
  assert.match(app, /`\$\{numberFormatter\.format\(windowHours\)\}시간 기준 · `/);
  assert.doesNotMatch(app, /getTopicTrendLabel|representative_posts|topic-trend/);
  assert.match(app, /const COMPACT_TOPIC_PANEL_QUERY = "\(max-width: 1759px\)"/);
  assert.match(app, /window\.matchMedia\(COMPACT_TOPIC_PANEL_QUERY\)\.matches/);
  assert.match(css, /--layout-max:\s*1184px/);
  assert.match(
    css,
    /\.page-shell\s*{[^}]*width:\s*min\(100%, var\(--layout-max\)\)[^}]*margin:\s*0 auto/s
  );
  assert.match(css, /\.archive-layout\s*{[^}]*grid-template-columns:\s*minmax\(0, 1fr\) 272px/s);
  assert.match(css, /\.topic-panel\s*{[^}]*position:\s*sticky/s);
  assert.match(css, /\.topic-panel-header\s*{[^}]*height:\s*var\(--board-head-height\)[^}]*padding:\s*0 var\(--space-3\)/s);
  assert.match(css, /\.topic-list\s*{[^}]*display:\s*flex[^}]*flex-wrap:\s*wrap[^}]*gap:\s*0 var\(--space-3\)/s);
  assert.match(css, /\.topic-item\s*{[^}]*display:\s*inline-flex[^}]*width:\s*auto[^}]*min-height:\s*28px/s);
  assert.match(css, /\.topic-item\[aria-pressed="true"\]/);
  assert.doesNotMatch(css, /\.topic-panel-eyebrow|\.topic-summary|\.topic-trend|\.topic-representative/);
  assert.match(
    css,
    /@media \(min-width:\s*1760px\)[\s\S]*\.archive-layout\s*{[^}]*width:\s*calc\(100% \+ 272px \+ var\(--space-4\)\)/
  );
  assert.match(
    css,
    /@media \(min-width:\s*1760px\)[\s\S]*\.archive-main\s*{[^}]*display:\s*contents[\s\S]*\.filter-shell\s*{[^}]*grid-row:\s*1[^}]*margin-bottom:\s*0[\s\S]*\.board-shell\s*{[^}]*grid-row:\s*2[\s\S]*\.topic-panel\s*{[^}]*grid-row:\s*2/
  );
  assert.match(
    css,
    /@media \(max-width:\s*1759px\)[\s\S]*\.archive-layout\s*{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)[\s\S]*\.topic-panel\s*{[^}]*order:\s*-1[\s\S]*\.topic-panel-toggle\s*{[^}]*display:\s*inline-flex/
  );
  assert.match(fixtureServer, /COMMUNITY_TOPIC_FIXTURES/);
  assert.match(fixtureServer, /window_hours:\s*24/);
  assert.match(fixtureServer, /!topicId \|\| post\.topic_ids\.includes\(topicId\)/);
});

test("switches article archives to a five-column feedback presentation", () => {
  assert.match(app, /getCurrentArchive\(\)\?\.content_kind === "article"/);
  assert.match(app, /document\.body\.dataset\.contentKind = mixedMode/);
  assert.match(app, /state\.minUpvotes = 0/);
  assert.match(app, /state\.minComments = 0/);
  assert.match(app, /state\.sortBy = "created_at"/);
  assert.match(app, /articleMode[\s\S]*\? "주제"[\s\S]*: "말머리"/);
  assert.match(app, /articleMode[\s\S]*\? "저장된 게임 기사"[\s\S]*: "저장된 커뮤니티 글"/);
  assert.match(app, /option\.hidden = articleMode/);
  assert.match(app, /if \(!articleMode\) \{\s*content\.append\(commentCount\)/);
  assert.match(app, /createSourceCell\(post\)/);
  assert.match(app, /inven: "inv"/);
  assert.match(app, /thisisgame: "tig"/);
  assert.match(app, /business: "biz"/);
  assert.match(app, /development: "dev"/);
  assert.match(app, /platform: "store"/);
  assert.match(app, /release: "launch"/);
  assert.match(app, /technology: "tech"/);
  assert.match(app, /other: "etc"/);
  assert.match(
    app,
    /if \(isArticleArchive\(\)\) \{\s*const label = getArticleSubjectLabel\(value\);\s*cell\.textContent = label;/
  );
  assert.match(css, /body\[data-content-kind="article"\] \.filter-upvotes/);
  assert.match(css, /body\[data-content-kind="article"\] \.cell-number/);
  assert.match(css, /body\[data-content-kind="article"\] \.cell-source\s*{[^}]*display:\s*block/s);
  assert.match(
    css,
    /body\[data-content-kind="article"\] \.post-row \.cell-subject\s*{[^}]*text-overflow:\s*ellipsis[^}]*white-space:\s*nowrap/s
  );
  assert.match(fixtureServer, /archive_key: "game-news"[\s\S]*content_kind: "article"/);
  assert.match(fixtureServer, /source_key: "game-news-inven"/);
  assert.match(fixtureServer, /source_key: "game-news-thisisgame"/);
  assert.match(fixtureServer, /const ARTICLE_SUBJECTS = Object\.freeze\(\[/);
  assert.match(fixtureServer, /"store"/);
  assert.doesNotMatch(fixtureServer, /"security"/);
  assert.match(fixtureServer, /"development"/);
  assert.match(fixtureServer, /"technology"/);
  assert.match(
    css,
    /body\[data-content-kind="article"\] \.board-row\s*{[^}]*grid-template-columns:\s*60px 40px minmax\(0, 1fr\) 92px 56px/s
  );
  assert.equal(
    (css.match(/grid-template-columns:\s*60px 40px/g) || []).length,
    3
  );
  assert.match(css, /\.cell-source\s*{[^}]*padding:\s*0 var\(--space-1\)[^}]*font-weight:\s*400/s);
  assert.match(html, /class="board-cell cell-feedback"[^>]*role="columnheader"[^>]*>평가/);
  assert.match(app, /FEEDBACK_RATINGS = Object\.freeze/);
  assert.match(app, /아주 흥미있음/);
  assert.match(app, /흥미는 있음/);
  assert.match(app, /별로 관심 없음/);
  assert.match(app, /아주 관심 없음/);
  assert.match(app, /목록에서 숨기고 강한 비선호로 기록/);
  assert.match(html, /id="feedback-dialog"/);
  assert.match(html, /id="feedback-article-title"/);
  assert.match(app, /button\.className = "feedback-open-button"/);
  assert.match(app, /button\.textContent = "평가"/);
  assert.match(app, /function openFeedbackDialog\(post, trigger\)/);
  assert.match(app, /elements\.feedbackDialogToolbar\.setAttribute\("aria-label"/);
  assert.match(app, /button\.setAttribute\("aria-pressed", "false"\)/);
  assert.match(css, /\.feedback-open-button\s*{[^}]*height:\s*24px/s);
  assert.match(css, /\.feedback-toolbar\s*{[^}]*grid-template-columns:\s*repeat\(5, minmax\(48px, 1fr\)\)/s);
  assert.match(
    css,
    /@media \(max-width:\s*768px\)[\s\S]*body\[data-content-kind="article"\] \.post-row\s*{[^}]*grid-template-columns:\s*60px 40px minmax\(0, 1fr\) 92px 56px/
  );
  assert.doesNotMatch(css, /\.post-row \.cell-feedback\s*{[^}]*grid-column:\s*1 \/ -1/s);
});

test("ships reversible hiding and a versioned preference document editor", () => {
  assert.match(html, /id="rules-open"/);
  assert.match(html, /id="hidden-open"/);
  assert.match(html, /id="rules-dialog"/);
  assert.match(html, /id="preference-document"/);
  assert.match(html, /id="preference-save"/);
  assert.match(html, /id="preference-reset"/);
  assert.match(html, /id="preference-clear"/);
  assert.match(html, /id="hidden-dialog"/);
  assert.match(html, /id="feedback-undo"/);
  assert.match(app, /postGameNewsJson\("\/api\/game-news\/feedback"/);
  assert.match(app, /postGameNewsJson\("\/api\/game-news\/visibility"/);
  assert.match(app, /fetchGameNewsJson\("\/api\/game-news\/preferences"/);
  assert.match(app, /postGameNewsJson\("\/api\/game-news\/preferences"/);
  assert.match(app, /base_version:\s*state\.preferenceDocument\.version/);
  assert.match(app, /action:\s*"restore"/);
  assert.match(app, /state\.feedbackByPost\.set/);
  assert.match(app, /"X-TodayCommunity-Write": "1"/);
  assert.doesNotMatch(html, /id="rule-strength"|id="rules-list"/);
  assert.doesNotMatch(app, /\/api\/game-news\/rules/);
  assert.doesNotMatch(app, /innerHTML\s*=/);
});

test("switches archive targets and pages with history-aware URLs", () => {
  assert.match(app, /target:\s*DEFAULT_TARGET/);
  assert.match(app, /target: state\.target/);
  assert.match(app, /params\.get\("target"\)/);
  assert.match(app, /Object\.assign\(state, DEFAULT_STATE\)[\s\S]*state\.target = normalizedTarget/);
  assert.match(app, /syncStateToUrl\(\{ replace: false \}\)/);
  assert.match(
    app,
    /function goToPage\(page\) \{[\s\S]*?syncStateToUrl\(\{ replace: false \}\);[\s\S]*?loadArchive\(\);/
  );
  assert.match(app, /function syncStateToUrl\(\{ replace = true \} = \{\}\)/);
  assert.match(app, /window\.history\[method\]\(null, "", url\)/);
  assert.match(app, /window\.addEventListener\("popstate"/);
  assert.match(app, /state\.page = 1/);
  assert.match(app, /elements\.archiveTitle\.textContent = `\$\{archive\.display_name\} 아카이브`/);
  assert.match(app, /document\.title = `\$\{archive\.display_name\} \| 오늘의 커뮤니티`/);
});

test("keeps source provenance when a display archive combines feeds", () => {
  assert.match(app, /Array\.isArray\(payload\?\.sources\)/);
  assert.match(app, /payload\?\.source[\s\S]*\[payload\.source\]/);
  assert.match(app, /run\.board_name \? `\$\{run\.board_name\} · \$\{runType\}`/);
  assert.match(app, /archive_key: "fmkorea-munich"[\s\S]*display_name: "뮌헨"/);
});

test("requests globally filtered, sorted, and paginated archive data", () => {
  for (const parameter of [
    "page",
    "page_size",
    "min_upvotes",
    "min_comments",
    "sort",
    "q",
    "subject",
    "topic",
  ]) {
    assert.match(app, new RegExp(`params\\.(?:set|toString)|${parameter}`));
    assert.ok(app.includes(parameter), `Expected ${parameter} in the dashboard request contract`);
  }

  assert.match(app, /pageSize:\s*30/);
  assert.match(app, /minUpvotes:\s*0/);
  assert.match(html, /id="upvotes-input"[^>]*min="0"[^>]*value="0"/);
  assert.match(app, /minUpvotes === null[\s\S]*DEFAULT_STATE\.minUpvotes/);
  assert.match(
    app,
    /state\.minUpvotes === DEFAULT_STATE\.minUpvotes \? null : state\.minUpvotes/
  );
  assert.match(app, /VALID_PAGE_SIZES = new Set\(\[20, 30, 50, 100\]\)/);
  assert.match(app, /summary\.filtered_posts/);
  assert.match(app, /renderPagination\(view\.pagination\)/);
  assert.doesNotMatch(app, /limit=100/);
});

test("preserves signed FMKorea recommendation counts in local rendering", () => {
  assert.match(
    app,
    /numberFormatter\.format\(normalizeSignedInteger\(post\.upvotes, 0\)\)/
  );
  assert.match(
    app,
    /normalizeSignedInteger\(right\.upvotes, 0\) - normalizeSignedInteger\(left\.upvotes, 0\)/
  );
  assert.match(
    app,
    /state\.minUpvotes > 0[\s\S]*normalizeSignedInteger\(post\.upvotes, 0\) < state\.minUpvotes/
  );
  assert.doesNotMatch(
    app,
    /normalizeNonNegativeNumber\((?:post|left|right)\.upvotes/
  );
});

test("filters by exact subjects from the complete saved set", () => {
  assert.match(app, /subject:\s*""/);
  assert.match(app, /params\.set\("subject", state\.subject\)/);
  assert.match(app, /state\.subject && normalizeSubject\(post\.subject\) !== state\.subject/);
  assert.match(app, /state\.subject = normalizeSubject\(elements\.subjectSelect\.value\)/);
  assert.match(app, /state\.subject = normalizeSubject\(params\.get\("subject"\)\)/);
  assert.match(app, /subject: state\.subject \|\| null/);
  assert.match(app, /Array\.isArray\(state\.archive\?\.subject_options\)/);
  assert.match(app, /state\.archive\.posts\.map\(\(post\) => post\.subject\)/);
  assert.match(app, /elements\.subjectSelect\.replaceChildren\(allOption, \.\.\.subjectOptions\)/);
  assert.match(app, /setSubjectControlValue\(state\.subject\)/);
  assert.match(app, /Array\.from\(elements\.subjectSelect\.options\)\.some/);
  assert.match(app, /elements\.subjectSelect\.append\(createSubjectOption\(value\)\)/);
  assert.match(app, /option\.textContent = value/);
  assert.match(app, /characters\.length <= 100 \? characters\.join\(""\) : ""/);
  assert.match(fixtureServer, /requestUrl\.searchParams\.get\("subject"\)/);
  assert.match(fixtureServer, /!subject \|\| post\.subject === subject/);
  assert.match(
    fixtureServer,
    /subject_options:\s*articleMode \|\| mixedMode[\s\S]*archiveSubjectOptions[\s\S]*subjectOptions/
  );
});

test("shows an archive-specific two-line collection summary", () => {
  assert.match(html, /디시인사이드 특이점이 온다 갤러리 인기글\.<br \/>/);
  assert.match(app, /"dcinside-singularity": "디시인사이드 특이점이 온다 갤러리 인기글\."/);
  assert.match(app, /"dcinside-agent-stack": "디시인사이드 AI 활용 갤러리 인기글\."/);
  assert.match(app, /"dcinside-zeus-pride": "디시인사이드 제우스 오만의 신 갤러리 인기글\."/);
  assert.match(app, /"fmkorea-munich": "에펨코리아 바이에른 뮌헨 관련 인기글\."/);
  assert.match(
    app,
    /elements\.sourceDescription\.replaceChildren\([\s\S]*document\.createElement\("br"\)[\s\S]*LIST_ONLY_DESCRIPTION/
  );
  assert.match(app, /본문 내용은 수집하지 않고 제목과 원문 링크 등 목록 정보만 수집합니다/);
  assert.doesNotMatch(app, /수집 기준:|추천수 \+ 댓글수\/|≥/);
});

test("renders untrusted archive data without HTML injection", () => {
  assert.doesNotMatch(app, /\.innerHTML\s*=/);
  assert.match(app, /document\.createElement\("a"\)/);
  assert.match(app, /String\(post\.title \|\| "제목 없음"\)/);
  assert.match(app, /titleText\.textContent = title/);
  assert.match(app, /content\.rel = "noreferrer noopener"/);
  assert.match(app, /getSafeHttpUrl/);
  assert.match(app, /\["http:", "https:"\]/);
  assert.match(app, /String\(post\?\.subject \|\| ""\)\.trim\(\)/);
  assert.match(app, /createSubjectCell\(post\)/);
  assert.match(app, /DESKTOP_SUBJECT_PREVIEW_LENGTH = 5/);
  assert.match(app, /MOBILE_SUBJECT_PREVIEW_LENGTH = 5/);
  assert.match(app, /new Intl\.Segmenter\("ko", \{ granularity: "grapheme" \}\)/);
  assert.match(app, /createCell\("", "cell-subject"\)/);
  assert.match(app, /subject-preview-desktop/);
  assert.match(app, /subject-preview-mobile/);
  assert.match(app, /cell\.setAttribute\("aria-label", value\)/);
  assert.doesNotMatch(app, /cell\.title = value/);
  assert.doesNotMatch(app, /subject-text/);
  assert.doesNotMatch(app, /post-subject|badge\.textContent/);
  assert.match(fixtureServer, /index === 0[\s\S]*\? ""/);
  assert.match(fixtureServer, /☕작업잡담/);
  assert.match(fixtureServer, /👨‍👩‍👧‍👦AI잡담/);
  assert.match(fixtureServer, /양자 컴퓨팅/);
});

test("keeps the collection drawer keyboard and focus contract", () => {
  assert.match(app, /runsDrawer\.showModal\(\)/);
  assert.match(app, /event\.key === "Escape"/);
  assert.match(app, /runsDrawer\.close\(\)/);
  assert.match(app, /runsOpen\.focus\(\)/);
  assert.match(app, /aria-expanded/);
});

test("moves page-change focus to visible content and follows the motion contract", () => {
  assert.match(html, /id="archive-title"[^>]*tabindex="-1"/);
  assert.match(app, /focusPageContentAfterLoad/);
  assert.match(app, /elements\.archiveTitle\.focus\(\{ preventScroll: true \}\)/);
  assert.doesNotMatch(css, /transition\s*:[^;]*(?:background-color|border-color)/s);
  assert.doesNotMatch(css, /transition\s*:[^;]*,\s*color\s+/s);
});

test("uses quick numbered pages and accessible previous-next controls", () => {
  assert.match(app, /PAGE_WINDOW_RADIUS\s*=\s*3/);
  assert.match(app, /createPageJumpForm\(pagination\.page, pagination\.total_pages\)/);
  assert.match(app, /pagination\.mode === "sequential"[\s\S]*renderSequentialPagination/);
  assert.match(app, /createCursorPageButton\([\s\S]*"이전"[\s\S]*pagination\.previous_cursor/);
  assert.match(app, /createCursorPageButton\([\s\S]*"다음"[\s\S]*pagination\.next_cursor/);
  assert.match(app, /previous\.disabled = !pagination\.has_previous/);
  assert.match(app, /next\.disabled = !pagination\.has_next/);
  assert.match(app, /pageList\.setAttribute\("aria-label", "글 목록 구간 이동"\)/);
  assert.match(app, /current\.setAttribute\("aria-current", "page"\)/);
  assert.match(app, /function goToCursor\(cursor, page\)/);
  assert.match(app, /state\.cursor = String\(cursor \|\| ""\)/);
  assert.match(app, /function buildApiUrl\(\{ page = state\.page, cursor = state\.cursor \}/);
  assert.match(app, /params\.set\("cursor", cursor\)/);
  assert.match(app, /state\.cursor = String\(params\.get\("cursor"\)/);
  assert.match(app, /cursor: state\.cursor \|\| null/);
  assert.match(app, /form\.setAttribute\("aria-label", "페이지 직접 이동"\)/);
  assert.match(app, /input\.type = "number"/);
  assert.match(app, /input\.min = "1"/);
  assert.match(app, /input\.max = String\(totalPages\)/);
  assert.match(app, /input\.step = "1"/);
  assert.match(app, /input\.required = true/);
  assert.match(app, /input\.setAttribute\([\s\S]*"aria-label"/);
  assert.doesNotMatch(app, /pagination-jump-(?:label|total)/);
  assert.match(app, /event\.key === "Enter"[\s\S]*submit\.click\(\)/);
  assert.match(app, /form\.addEventListener\("submit"/);
  assert.match(app, /const page = parsePageJump\(input\.value, totalPages\)/);
  assert.match(app, /goToPage\(page\)/);
  assert.match(css, /--control-height:\s*40px/);
  assert.match(css, /--pagination-control-size:\s*36px/);
  assert.match(
    css,
    /\.pagination-button\s*{[^}]*min-width:\s*var\(--pagination-control-size\)[^}]*height:\s*var\(--pagination-control-size\)/s
  );
  assert.doesNotMatch(css, /\.pagination-direction/);
  assert.match(css, /\.pagination-sequential\s*{[^}]*display:\s*flex/s);
  assert.match(css, /\.pagination-current\s*{[^}]*font-variant-numeric:\s*tabular-nums/s);
  assert.match(css, /\.pagination-jump\s*{[^}]*margin-right:\s*var\(--space-3\)/s);
  assert.match(css, /\.pagination-jump-input\s*{[^}]*height:\s*var\(--pagination-control-size\)/s);
  assert.match(
    css,
    /@media \(max-width:\s*1010px\)[\s\S]*\.pagination\s*{[^}]*flex-wrap:\s*nowrap[^}]*align-items:\s*flex-start[\s\S]*\.pagination-pages\s*{[^}]*overflow-x:\s*auto[\s\S]*\.pagination-jump\s*{[^}]*margin-top:\s*var\(--space-1\)/
  );
  assert.match(
    css,
    /@media \(max-width:\s*520px\)[\s\S]*\.board-footer\s*{[^}]*position:\s*relative[^}]*display:\s*block[\s\S]*\.pagination\s*{[^}]*--pagination-control-size:\s*28px[^}]*display:\s*block[\s\S]*\.pagination-jump\s*{[^}]*position:\s*absolute[^}]*top:\s*var\(--space-3\)[^}]*right:\s*var\(--space-3\)[^}]*margin:\s*0/
  );
  assert.match(
    css,
    /@media \(max-width:\s*520px\)[\s\S]*\.pagination-button\s*{[^}]*padding:\s*0 var\(--space-1\)[^}]*font-size:\s*var\(--text-xs\)[\s\S]*\.pagination-jump-input\s*{[^}]*width:\s*var\(--space-12\)[^}]*font-size:\s*var\(--text-xs\)/
  );
  assert.doesNotMatch(css, /\.pagination-page:not\(\[aria-current="page"\]\)[^{]*{[^}]*display:\s*none/);
});

test("keeps action states inside an ARIA table cell", () => {
  assert.match(app, /cell\.setAttribute\("role", "cell"\)[\s\S]*cell\.append\(button\)/);
  assert.doesNotMatch(app, /row\.append\(button\)/);
});

test("locks desktop rows and responsive column reduction", () => {
  assert.match(css, /--board-row-height:\s*44px/);
  assert.match(css, /\.post-row\s*{[\s\S]*height:\s*var\(--board-row-height\)/);
  assert.doesNotMatch(html, /class="board-cell cell-rule"/);
  assert.doesNotMatch(app, /getQualificationLabel|"cell-rule"/);
  assert.match(css, /@media \(max-width:\s*768px\)/);
  assert.match(css, /@media \(max-width:\s*520px\)/);
  assert.match(css, /\.cell-number\s*{\s*display:\s*none/);
  assert.match(css, /\.cell-date\s*{\s*display:\s*none/);
  assert.match(css, /overflow-x:\s*hidden/);
  assert.match(css, /text-overflow:\s*ellipsis/);
  assert.match(css, /grid-template-columns:\s*88px 72px minmax\(0, 1fr\) 64px 104px/);
  assert.match(css, /\.cell-subject\s*{[^}]*text-overflow:\s*clip[^}]*white-space:\s*nowrap/);
  assert.match(css, /body\[data-content-kind="article"\] \.post-row \.cell-subject\s*{[^}]*text-overflow:\s*ellipsis/);
  assert.match(css, /\.post-row \.cell-subject:empty::before\s*{\s*content:\s*"\\00a0"/);
  assert.match(css, /@media \(max-width:\s*520px\)[\s\S]*grid-template-columns:\s*72px minmax\(0, 1fr\) 48px/);
  assert.match(css, /@media \(max-width:\s*520px\)[\s\S]*html\s*{[^}]*font-size:\s*96\.875%/);
  assert.match(css, /@media \(max-width:\s*520px\)[\s\S]*\.board-cell\s*{[^}]*padding:\s*0 5px/);
  assert.doesNotMatch(css, /\.board-cell\s*{[^}]*display:\s*flex/);
  assert.doesNotMatch(css, /\.board-cell\s*{[^}]*height:\s*100%/);
  assert.match(css, /\.board-cell \+ \.board-cell\s*{[\s\S]*border-left:\s*1px solid/);
  assert.doesNotMatch(css, /\.post-subject/);
});

test("moves comment counts beside ellipsized titles", () => {
  assert.doesNotMatch(app, /"cell-comments numeric-cell"/);
  assert.match(app, /commentCount\.className = "post-comment-count"/);
  assert.match(app, /commentCount\.textContent = `\[\$\{numberFormatter\.format\(comments\)\}\]`/);
  assert.match(app, /content\.append\(titleText\)/);
  assert.match(app, /if \(!articleMode\) \{\s*content\.append\(commentCount\)/);
  assert.match(app, /commentDescription\.className = "visually-hidden"/);
  assert.match(app, /commentDescription\.textContent = `댓글 \$\{comments\}개`/);
  assert.match(css, /\.visually-hidden\s*{[^}]*position:\s*absolute[^}]*clip:\s*rect\(0 0 0 0\)/s);
  assert.match(css, /\.post-title-text\s*{[^}]*flex:\s*0 1 auto[^}]*text-overflow:\s*ellipsis/s);
  assert.match(css, /\.post-comment-count\s*{[^}]*flex:\s*0 0 auto[^}]*color:\s*var\(--color-primary\)/s);
});

test("collapses filters by default on every screen", () => {
  assert.match(html, /id="filter-toggle"[\s\S]*aria-controls="filter-form"[\s\S]*aria-expanded="false"/);
  assert.match(app, /setFiltersExpanded\(false\)/);
  assert.doesNotMatch(app, /setMobileFiltersExpanded|hasActiveFilterState/);
  assert.match(app, /filterToggle\.addEventListener\("click"/);
  assert.match(css, /\.filter-toggle\s*{[^}]*display:\s*flex/);
  assert.doesNotMatch(css, /\.filter-toggle\s*{\s*display:\s*none/);
  assert.match(
    css,
    /\.filter-form\s*{[^}]*display:\s*none[\s\S]*\.filter-shell\.is-filter-expanded \.filter-form\s*{[^}]*display:\s*grid/
  );
});

test("wraps the expanded filter bar only when a single row no longer fits", () => {
  assert.match(css, /grid-template-columns:\s*minmax\(220px, 1\.7fr\) repeat\(5, minmax\(116px, 0\.7fr\)\) auto/);
  assert.match(css, /@media \(min-width:\s*769px\) and \(max-width:\s*1010px\)/);
  assert.match(css, /@media \(min-width:\s*769px\) and \(max-width:\s*1010px\)[\s\S]*grid-template-columns:\s*repeat\(4, minmax\(0, 1fr\)\)/);
  assert.match(css, /@media \(min-width:\s*769px\) and \(max-width:\s*1010px\)[\s\S]*\.filter-search\s*{[\s\S]*grid-column:\s*span 2/);
});

test("uses the declared design system without generic visual defaults", () => {
  if (design !== null) {
    assert.match(design, /## 1\. Atmosphere \/ signature/);
    assert.match(design, /## 7\. Depth/);
  }
  assert.match(css, /--color-primary:\s*#244c93/i);
  assert.match(css, /--font-sans:\s*"Malgun Gothic"/);
  assert.doesNotMatch(css, /\b(?:Inter|Roboto)\b/i);
  assert.doesNotMatch(css, /(?:linear|radial)-gradient/i);
  assert.doesNotMatch(css, /backdrop-filter/i);
  assert.doesNotMatch(css, /999px/);
});

test("visible copy avoids banned punctuation and generic AI phrases", () => {
  const visibleSources = `${html}\n${app}`;
  assert.doesNotMatch(visibleSources, /—/);
  assert.doesNotMatch(
    visibleSources,
    /\b(?:Elevate|Seamless|Unleash|Delve|Empower|Supercharge)\b/i
  );
});
