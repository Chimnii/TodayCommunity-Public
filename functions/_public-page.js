const ORIGIN = "https://todaycommunity.pages.dev";
const DEFAULT_TARGET = "dcinside-singularity";
const PUBLIC_ARCHIVES = Object.freeze({
  "dcinside-singularity": ["특이점이 온다", "디시인사이드 특이점이 온다 갤러리 (특갤) 인기글.", "community"],
  "dcinside-agent-stack": ["AI 활용", "디시인사이드 AI 활용 갤러리 (에활갤) 인기글.", "community"],
  "dcinside-zeus-pride": ["제우스 오만의 신", "디시인사이드 제우스 오만의 신 갤러리 인기글.", "community"],
  "fmkorea-munich": ["뮌헨", "에펨코리아 바이에른 뮌헨 관련 인기글.", "community"],
  "game-news": ["게임 뉴스", "게임 신작, 인터뷰와 업계 동향을 휴리스틱하게 선별한 기사.", "article"],
  all: ["모두", "모든 공개 아카이브의 글을 최신순으로 모았습니다.", "mixed"],
});
const ARCHIVE_LABELS = { "dcinside-singularity": "특이점", "dcinside-agent-stack": "AI활용",
  "dcinside-zeus-pride": "제우스", "fmkorea-munich": "Bayern", "game-news": "게임뉴스" };
const SUBJECT_LABELS = { business: "biz", development: "dev", platform: "store",
  release: "launch", technology: "tech", other: "etc" };
const AUTH_COOKIE = /(?:^|;\s*)(?:__Host-tc_authenticated|__Host-tc_admin)=/;
const number = new Intl.NumberFormat("ko-KR");
const date = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul", year: "2-digit", month: "2-digit", day: "2-digit" });
const time = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, character => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]);
}

function serializeJson(value) {
  return JSON.stringify(value).replace(/[<>&\u2028\u2029]/g, character => `\\u${character.charCodeAt(0).toString(16).padStart(4, "0")}`);
}

function count(value) {
  const result = Number(value);
  return Number.isSafeInteger(result) ? result : 0;
}

function formatDate(value, formatter) {
  const parsed = new Date(value);
  return value && Number.isFinite(parsed.getTime()) ? formatter.format(parsed) : "기록 없음";
}

function safeUrl(value) {
  try {
    const url = new URL(String(value));
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

export function publicPageState(request) {
  const url = new URL(request.url);
  const rawTarget = url.searchParams.get("target");
  const target = rawTarget === null ? DEFAULT_TARGET : rawTarget;
  const known = Object.hasOwn(PUBLIC_ARCHIVES, target);
  const favorites = target === "favorites";
  const privateRequest = AUTH_COOKIE.test(request.headers.get("cookie") || "") || request.headers.has("authorization");
  const baseQuery = ![...url.searchParams.keys()].some(key => key !== "target") && url.searchParams.getAll("target").length <= 1;
  const canonical = known && target !== DEFAULT_TARGET ? `${ORIGIN}/?target=${target}` : `${ORIGIN}/`;
  const [label, lead, kind] = known ? PUBLIC_ARCHIVES[target] : favorites
    ? ["즐겨찾기", "시크릿 사용자들이 함께 저장한 글.", "mixed"]
    : ["아카이브를 찾을 수 없습니다", "아카이브 탭에서 공개 목록을 선택해 주세요.", "community"];
  const description = known ? `${lead} ${kind === "article" ? "제목과 출처를 살펴보고 원문 기사로 이동하세요."
    : kind === "mixed" ? "관심 있는 주제의 제목과 원문 링크를 한곳에서 확인하세요."
      : "제목·말머리·추천·댓글 정보를 살펴보고 원문으로 이동하세요."}` : lead;
  return { target, known, favorites, privateRequest, canonical, label, lead, kind, description,
    title: "오늘의 커뮤니티",
    robots: !known ? "noindex, nofollow" : privateRequest || !baseQuery || url.origin !== ORIGIN ? "noindex, follow" : "index, follow",
    renderArchive: known && baseQuery && !privateRequest,
    status: known || favorites ? 200 : 404,
  };
}

function cell(value, className) {
  return `<span class="board-cell ${className}" role="cell">${escapeHtml(value)}</span>`;
}

function renderPosts(payload, page) {
  if (!payload.posts.length) return '<div class="board-state" role="row"><span role="cell">아직 저장된 글이 없습니다.</span></div>';
  return payload.posts.map(post => {
    const article = post.archive_key === "game-news" || page.kind === "article";
    const source = payload.sources?.find(source => source.source_key === post.source_key);
    const sourceKey = String(source?.site_name || post.source_key || "").toLowerCase().replace(/^game-news-/, "");
    const sourceLabel = ({ inven: "inv", thisisgame: "tig" })[sourceKey] || sourceKey.slice(0, 3) || "-";
    const subject = article ? SUBJECT_LABELS[post.subject] || post.subject || "-" : String(post.subject || "");
    const subjectCell = cell(page.kind === "mixed" && article ? `${sourceLabel}-${subject}` : subject, "cell-subject");
    const sourceCell = cell(page.kind === "mixed" ? ARCHIVE_LABELS[post.archive_key] || "-" : sourceLabel, "cell-source");
    const title = String(post.title || "제목 없음");
    const url = safeUrl(post.post_url);
    const comments = Math.max(0, count(post.comments));
    const titleContent = `<span class="post-title-text">${escapeHtml(title)}</span>${!article ? `<span class="post-comment-count" aria-hidden="true">[${number.format(comments)}]</span>` : ""}`;
    const titleCell = `<span class="board-cell cell-title" role="cell">${url
      ? `<a class="post-title-content" href="${escapeHtml(url)}" target="_blank" rel="noreferrer noopener" title="${escapeHtml(title)}" aria-label="${escapeHtml(article ? `${title}, 원문 열기` : `${title}, 댓글 ${comments}개, 원문 열기`)}">${titleContent}</a>`
      : `<span class="post-title-content">${titleContent}</span>`}</span>`;
    return `<article class="board-row post-row" role="row">${cell(post.external_post_id || "-", "cell-number numeric-cell")}${page.kind === "mixed" ? sourceCell + subjectCell : subjectCell + sourceCell}${titleCell}${cell(page.kind === "mixed" && article ? "-" : number.format(count(post.upvotes)), "cell-upvotes numeric-cell")}${cell(formatDate(post.created_at, date).replaceAll("-", "."), "cell-date numeric-cell")}${cell("", "cell-feedback")}${cell("", "cell-favorite")}</article>`;
  }).join("");
}

function basicApiUrl(target) {
  return `/api/archive?${new URLSearchParams({ target, page: "1", page_size: "30", min_upvotes: "0", min_comments: "0", sort: "created_at" })}`;
}

// The response is never cached separately from /api/archive. The same public
// handler, cache keys and active/public/hidden filters serve people and crawlers.
export async function renderPublicPage(context, loadArchive) {
  const page = publicPageState(context.request);
  const templateHeaders = new Headers(context.request.headers);
  // A conditional/range request must not bypass the dynamic document policy by
  // producing a static-asset 304/206 before we rewrite metadata and content.
  for (const name of ["if-none-match", "if-modified-since", "if-match", "if-unmodified-since", "if-range", "range"]) templateHeaders.delete(name);
  const template = await context.next(new Request(context.request, { method: "GET", headers: templateHeaders }));
  if (!template.ok || !template.headers.get("content-type")?.includes("text/html")) return template;

  let payload = null;
  let bootstrap = null;
  if (page.renderArchive && context.request.method !== "HEAD") {
    const apiUrl = basicApiUrl(page.target);
    const headers = new Headers({ accept: "application/json" });
    // Preserve explicit refreshes, but never forward cookies or private queries.
    for (const name of ["cache-control", "x-tc-refresh"]) {
      if (context.request.headers.has(name)) headers.set(name, context.request.headers.get(name));
    }
    try {
      const response = await loadArchive({ ...context, request: new Request(new URL(apiUrl, context.request.url), { headers }) });
      if (!response.ok) throw new Error("Public archive unavailable");
      payload = await response.json();
      if (payload?.target !== page.target || !Array.isArray(payload.posts) || payload.posts.length > 30) throw new Error("Invalid public archive response");
      const expires = Number(response.headers.get("x-tc-expires-at"));
      // Old edge entries without this header are rendered, but never reused by
      // the browser. No new cache layer may extend the existing 120-second TTL.
      bootstrap = { version: 1, api_url: apiUrl, payload,
        expires_at: Number.isFinite(expires) && expires > 0 ? Math.min(Date.now() + 15_000, expires) : 0 };
    } catch {
      payload = null;
      page.status = 503;
      page.robots = "noindex, follow";
    }
  }

  const headers = new Headers(template.headers);
  headers.set("cache-control", "private, no-store");
  headers.set("x-robots-tag", page.robots);
  headers.set("x-content-type-options", "nosniff");
  for (const name of ["etag", "last-modified", "content-length"]) headers.delete(name);
  if (page.status === 503) headers.set("retry-after", "60");
  const response = new Response(context.request.method === "HEAD" ? null : template.body, { status: page.status, headers });
  if (context.request.method === "HEAD") return response;

  const rewriter = new HTMLRewriter()
    .on("title", { element(element) { element.setInnerContent(page.title); } })
    .on('meta[name="description"]', { element(element) { element.setAttribute("content", page.description); } })
    .on('link[rel="canonical"]', { element(element) { element.setAttribute("href", page.canonical); } })
    .on('meta[name="twitter:title"]', { element(element) { element.setAttribute("content", page.title); } })
    .on('meta[name="twitter:description"]', { element(element) { element.setAttribute("content", page.description); } })
    .on('meta[name="robots"], meta[property="og:title"], meta[property="og:description"], meta[property="og:url"], meta[property="og:type"], meta[property="og:site_name"]', { element(element) { element.remove(); } })
    .on("head", { element(element) {
      element.append(`<meta name="robots" content="${page.robots}"><meta property="og:title" content="${escapeHtml(page.title)}"><meta property="og:description" content="${escapeHtml(page.description)}"><meta property="og:url" content="${escapeHtml(page.canonical)}"><meta property="og:type" content="website"><meta property="og:site_name" content="오늘의 커뮤니티">${bootstrap ? `<script id="archive-bootstrap" type="application/json">${serializeJson(bootstrap)}</script>` : ""}`, { html: true });
    } })
    .on("body", { element(element) {
      element.setAttribute("data-content-kind", page.kind);
      if (page.privateRequest) element.setAttribute("data-private-request", "true");
    } })
    .on("#archive-title", { element(element) { element.setInnerContent(page.known ? `${page.label} 아카이브` : page.label); } })
    .on("#source-description", { element(element) { element.setInnerContent(`${escapeHtml(page.lead)}<br>본문 내용은 수집하지 않고 제목과 원문 링크 등 목록 정보만 수집합니다.`, { html: true }); } })
    .on("#archive-tabs a", { element(element) {
      const selected = new URL(element.getAttribute("href") || "/", ORIGIN).searchParams.get("target") === page.target;
      element.setAttribute("aria-selected", String(selected));
      element.setAttribute("tabindex", selected ? "0" : "-1");
    } });

  if (payload) {
    const total = Math.max(0, count(payload.summary?.total_posts));
    rewriter.on("#posts", { element(element) {
      element.setInnerContent(renderPosts(payload, page), { html: true });
      element.setAttribute("style", `--reserved-row-count: ${Math.max(3, payload.posts.length)}`);
    } })
      .on("#archive-board", { element(element) { element.setAttribute("aria-busy", "false"); element.setAttribute("aria-label", `${page.label} 저장 글`); } })
      .on("#summary-total", { element(element) { element.setInnerContent(number.format(total)); } })
      .on("#summary-latest", { element(element) { element.setInnerContent(formatDate(payload.summary?.latest_seen_at, time)); } })
      .on("#result-count", { element(element) { element.setInnerContent(`${number.format(total)}개 저장 · 최신순`); } })
      .on("#range-summary", { element(element) { element.setInnerContent(payload.posts.length ? `최근 글 1–${payload.posts.length}개 표시` : "표시할 글이 없습니다."); } });
    if (page.kind === "mixed") {
      // Match the mixed table's source-before-subject order before JS hydrates.
      rewriter.on("#board-header-row", { element(element) {
        element.setInnerContent('<span class="board-cell cell-number" id="number-column-label" role="columnheader">번호</span><span class="board-cell cell-source" id="source-column-label" role="columnheader">소속</span><span class="board-cell cell-subject" id="subject-column-label" role="columnheader">말머리/주제</span><span class="board-cell cell-title" id="title-column-label" role="columnheader">제목</span><span class="board-cell cell-upvotes" id="upvotes-column-label" role="columnheader">추천</span><span class="board-cell cell-date" id="date-column-label" role="columnheader">작성일</span><span class="board-cell cell-feedback" id="feedback-column-label" role="columnheader">평가</span><span class="board-cell cell-favorite" id="favorite-column-label" role="columnheader" aria-label="즐겨찾기"><span aria-hidden="true">☆</span></span>', { html: true });
      } });
    } else if (page.kind === "article") {
      rewriter.on("#subject-column-label", { element(element) { element.setInnerContent("주제"); } })
        .on("#date-column-label", { element(element) { element.setInnerContent("발행일"); } });
    }
  } else if (page.status >= 400) {
    const message = page.status === 404 ? "요청한 아카이브를 찾을 수 없습니다. 위의 탭에서 공개 목록을 선택해 주세요." : "목록을 잠시 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.";
    rewriter.on("#posts", { element(element) { element.setInnerContent(`<div class="board-state" role="row"><span role="cell">${message}</span></div>`, { html: true }); } })
      .on("#result-count", { element(element) { element.setInnerContent(message); } });
  }
  return rewriter.transform(response);
}
