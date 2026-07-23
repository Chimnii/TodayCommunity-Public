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
    /cell-number" role="columnheader">번호<\/span>[\s\S]*cell-subject" role="columnheader">말머리<\/span>[\s\S]*cell-title" role="columnheader">제목<\/span>/
  );
  assert.match(
    html,
    /cell-title" role="columnheader">제목<\/span>[\s\S]*cell-upvotes" role="columnheader">추천<\/span>[\s\S]*cell-date" role="columnheader">작성일<\/span>/
  );
  assert.doesNotMatch(html, /cell-comments" role="columnheader"/);
  assert.match(
    html,
    /<h1><a class="masthead-home" href="\/">오늘의 커뮤니티<\/a><\/h1>/
  );
});

test("ships three accessible archive tabs and replaces them from the API catalog", () => {
  assert.match(html, /id="archive-tabs"[^>]*role="tablist"/);
  assert.match(html, /role="tab"[\s\S]*href="\/?\?target=dcinside-singularity"/);
  assert.match(html, /href="\/?\?target=dcinside-agent-stack"/);
  assert.match(html, /href="\/?\?target=fmkorea-munich"/);
  assert.match(html, />특이점이 온다 갤<\/a>/);
  assert.match(html, />AI 활용 갤<\/a>/);
  assert.match(html, />뮌헨 소식 \(펨코\)<\/a>/);

  assert.match(app, /Array\.isArray\(state\.archive\?\.archives\)/);
  assert.match(app, /getAvailableArchives\(\)\.map/);
  assert.match(app, /tab\.setAttribute\("role", "tab"\)/);
  assert.match(app, /tab\.setAttribute\("aria-selected", String\(key === state\.target\)\)/);
  assert.match(app, /\["ArrowLeft", "ArrowRight", "Home", "End"\]/);
  assert.match(app, /"dcinside-singularity": "특이점이 온다 갤"/);
  assert.match(app, /"dcinside-agent-stack": "AI 활용 갤"/);
  assert.match(app, /"fmkorea-munich": "뮌헨 소식 \(펨코\)"/);
  assert.match(app, /ARCHIVE_TAB_LABELS\[key\] \|\| String\(archive\.display_name \|\| key\)/);
  assert.match(css, /\.archive-tab\[aria-selected="true"\]/);
});

test("switches archive targets with clean filters and history-aware URLs", () => {
  assert.match(app, /target:\s*DEFAULT_TARGET/);
  assert.match(app, /target: state\.target/);
  assert.match(app, /params\.get\("target"\)/);
  assert.match(app, /Object\.assign\(state, DEFAULT_STATE\)[\s\S]*state\.target = normalizedTarget/);
  assert.match(app, /syncStateToUrl\(\{ replace: false \}\)/);
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
  assert.match(fixtureServer, /subject_options: subjectOptions/);
});

test("shows a stable collection summary without volatile numeric thresholds", () => {
  assert.match(app, /추천수 또는 댓글수가 일정 조건을 만족하는 글/);
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
  assert.match(app, /String\(subject \|\| ""\)\.trim\(\)/);
  assert.match(app, /createSubjectCell\(post\.subject\)/);
  assert.match(app, /DESKTOP_SUBJECT_PREVIEW_LENGTH = 5/);
  assert.match(app, /MOBILE_SUBJECT_PREVIEW_LENGTH = 3/);
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

test("supports compact direct page jumps and a seven-page window", () => {
  assert.match(app, /PAGE_WINDOW_RADIUS\s*=\s*3/);
  assert.match(app, /createPageJumpForm\(pagination\.page, pagination\.total_pages\)/);
  assert.doesNotMatch(app, /createPageButton\("(?:이전|다음)"/);
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
  assert.match(css, /\.pagination-jump\s*{[^}]*margin-right:\s*var\(--space-3\)/s);
  assert.match(css, /\.pagination-jump-input\s*{[^}]*height:\s*var\(--pagination-control-size\)/s);
  assert.match(
    css,
    /@media \(max-width:\s*1010px\)[\s\S]*\.pagination\s*{[^}]*flex-wrap:\s*nowrap[^}]*align-items:\s*flex-start[\s\S]*\.pagination-pages\s*{[^}]*overflow-x:\s*auto[\s\S]*\.pagination-jump\s*{[^}]*margin-top:\s*var\(--space-1\)/
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
  assert.doesNotMatch(css, /\.cell-subject\s*{[^}]*text-overflow:\s*ellipsis/);
  assert.match(css, /\.post-row \.cell-subject:empty::before\s*{\s*content:\s*"\\00a0"/);
  assert.match(css, /@media \(max-width:\s*520px\)[\s\S]*grid-template-columns:\s*64px minmax\(0, 1fr\) 48px/);
  assert.doesNotMatch(css, /\.board-cell\s*{[^}]*display:\s*flex/);
  assert.doesNotMatch(css, /\.board-cell\s*{[^}]*height:\s*100%/);
  assert.match(css, /\.board-cell \+ \.board-cell\s*{[\s\S]*border-left:\s*1px solid/);
  assert.doesNotMatch(css, /\.post-subject/);
});

test("moves comment counts beside ellipsized titles", () => {
  assert.doesNotMatch(app, /"cell-comments numeric-cell"/);
  assert.match(app, /commentCount\.className = "post-comment-count"/);
  assert.match(app, /commentCount\.textContent = `\[\$\{numberFormatter\.format\(comments\)\}\]`/);
  assert.match(app, /content\.append\(titleText, commentCount\)/);
  assert.match(app, /commentDescription\.className = "visually-hidden"/);
  assert.match(app, /commentDescription\.textContent = `댓글 \$\{comments\}개`/);
  assert.match(css, /\.visually-hidden\s*{[^}]*position:\s*absolute[^}]*clip:\s*rect\(0 0 0 0\)/s);
  assert.match(css, /\.post-title-text\s*{[^}]*flex:\s*0 1 auto[^}]*text-overflow:\s*ellipsis/s);
  assert.match(css, /\.post-comment-count\s*{[^}]*flex:\s*0 0 auto[^}]*color:\s*var\(--color-primary\)/s);
});

test("collapses filters only on compact mobile screens", () => {
  assert.match(html, /id="filter-toggle"[\s\S]*aria-controls="filter-form"[\s\S]*aria-expanded="false"/);
  assert.match(app, /setMobileFiltersExpanded\(hasActiveFilterState\(\)\)/);
  assert.match(app, /filterToggle\.addEventListener\("click"/);
  assert.match(css, /\.filter-toggle\s*{\s*display:\s*none/);
  assert.match(
    css,
    /@media \(max-width:\s*520px\)[\s\S]*\.filter-toggle\s*{[^}]*display:\s*flex[\s\S]*\.filter-form\s*{[^}]*display:\s*none[\s\S]*\.filter-shell\.is-filter-expanded \.filter-form\s*{[^}]*display:\s*grid/
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
