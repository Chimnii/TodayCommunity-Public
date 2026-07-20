const TARGET_KEY = "dcinside-singularity";
const SOURCE_DESCRIPTION =
  "추천수 또는 댓글수가 일정 조건을 만족하는 글을 모읍니다. 본문 내용은 수집하지 않고 제목과 원문 링크 등 목록 정보만 수집합니다.";
const DEFAULT_STATE = Object.freeze({
  search: "",
  subject: "",
  minUpvotes: 0,
  minComments: 0,
  sortBy: "created_at",
  page: 1,
  pageSize: 30,
});
const VALID_SORTS = new Set(["created_at", "upvotes", "comments"]);
const VALID_PAGE_SIZES = new Set([20, 30, 50, 100]);
const SUBJECT_PREVIEW_LENGTH = 3;
const PAGE_WINDOW_RADIUS = 3;
const subjectSegmenter = typeof Intl.Segmenter === "function"
  ? new Intl.Segmenter("ko", { granularity: "grapheme" })
  : null;

const state = {
  ...DEFAULT_STATE,
  archive: null,
  dataSource: "unknown",
  activeRequest: null,
  filterTimer: null,
  focusPageContentAfterLoad: false,
};

const elements = {
  sourceDescription: document.querySelector("#source-description"),
  summaryTotal: document.querySelector("#summary-total"),
  summaryLatest: document.querySelector("#summary-latest"),
  runCount: document.querySelector("#run-count"),
  runsOpen: document.querySelector("#runs-open"),
  runsClose: document.querySelector("#runs-close"),
  runsDrawer: document.querySelector("#runs-drawer"),
  runs: document.querySelector("#runs"),
  archiveTitle: document.querySelector("#archive-title"),
  board: document.querySelector("#archive-board"),
  posts: document.querySelector("#posts"),
  resultCount: document.querySelector("#result-count"),
  rangeSummary: document.querySelector("#range-summary"),
  pagination: document.querySelector("#pagination"),
  dataNotice: document.querySelector("#data-notice"),
  searchInput: document.querySelector("#search-input"),
  subjectSelect: document.querySelector("#subject-select"),
  upvotesInput: document.querySelector("#upvotes-input"),
  commentsInput: document.querySelector("#comments-input"),
  sortSelect: document.querySelector("#sort-select"),
  pageSizeSelect: document.querySelector("#page-size-select"),
  filterForm: document.querySelector("#filter-form"),
};

const numberFormatter = new Intl.NumberFormat("ko-KR");
const dateTimeFormatter = new Intl.DateTimeFormat("ko-KR", {
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function initialize() {
  hydrateStateFromUrl();
  writeStateToControls();
  bindEvents();
  loadArchive();
}

async function loadArchive() {
  if (state.activeRequest) {
    state.activeRequest.abort();
  }

  const controller = new AbortController();
  state.activeRequest = controller;
  renderLoadingState();

  try {
    const response = await fetch(buildApiUrl(), {
      cache: "no-store",
      headers: { accept: "application/json" },
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const payload = await response.json();
    if (controller.signal.aborted) {
      return;
    }

    state.archive = payload;
    state.dataSource = "live";

    const currentPage = normalizePositiveNumber(payload.pagination?.page, state.page);
    if (currentPage !== state.page) {
      state.page = currentPage;
      syncStateToUrl();
    }

    render();
  } catch (error) {
    if (error.name === "AbortError") {
      return;
    }

    const fallback = window.__TODAY_COMMUNITY_ARCHIVE__;
    if (fallback) {
      state.archive = fallback;
      state.dataSource = "fallback";
    } else {
      state.archive = {
        source: null,
        summary: { total_posts: 0, filtered_posts: 0, recent_runs: 0 },
        runs: [],
        posts: [],
        error: "라이브 데이터와 로컬 스냅샷을 모두 읽지 못했습니다.",
      };
      state.dataSource = "unavailable";
    }
    render();
  } finally {
    if (state.activeRequest === controller) {
      state.activeRequest = null;
    }
  }
}

function buildApiUrl() {
  const params = new URLSearchParams({
    target: TARGET_KEY,
    page: String(state.page),
    page_size: String(state.pageSize),
    min_upvotes: String(state.minUpvotes),
    min_comments: String(state.minComments),
    sort: state.sortBy,
  });

  if (state.search) {
    params.set("q", state.search);
  }
  if (state.subject) {
    params.set("subject", state.subject);
  }

  return `/api/archive?${params.toString()}`;
}

function render() {
  const view = getViewModel();
  elements.board.setAttribute("aria-busy", "false");
  renderSubjectOptions();
  renderSummary(view);
  renderNotice();
  renderRuns();
  renderPosts(view.posts);
  renderResultStatus(view);
  renderPagination(view.pagination);
  restorePageChangeFocus();
}

function getViewModel() {
  if (state.dataSource !== "live") {
    return getLocalViewModel();
  }

  const summary = state.archive.summary || {};
  const posts = Array.isArray(state.archive.posts) ? state.archive.posts : [];
  const pagination = normalizePagination(
    state.archive.pagination,
    normalizeNonNegativeNumber(summary.filtered_posts, posts.length),
    posts.length
  );

  return {
    posts,
    totalPosts: normalizeNonNegativeNumber(summary.total_posts, posts.length),
    filteredPosts: normalizeNonNegativeNumber(summary.filtered_posts, posts.length),
    pagination,
  };
}

function getLocalViewModel() {
  const allPosts = Array.isArray(state.archive?.posts) ? state.archive.posts : [];
  const search = state.search.trim().toLocaleLowerCase("ko-KR");
  const filtered = [...allPosts]
    .filter((post) => {
      if (
        normalizeNonNegativeNumber(post.upvotes, 0) < state.minUpvotes ||
        normalizeNonNegativeNumber(post.comments, 0) < state.minComments
      ) {
        return false;
      }

      if (state.subject && normalizeSubject(post.subject) !== state.subject) {
        return false;
      }

      if (!search) {
        return true;
      }

      return String(post.title || "").toLocaleLowerCase("ko-KR").includes(search);
    })
    .sort(comparePosts);

  const totalPages = filtered.length === 0 ? 0 : Math.ceil(filtered.length / state.pageSize);
  const safePage = totalPages === 0 ? 1 : Math.min(state.page, totalPages);
  if (safePage !== state.page) {
    state.page = safePage;
    syncStateToUrl();
  }

  const offset = (safePage - 1) * state.pageSize;
  const posts = filtered.slice(offset, offset + state.pageSize);

  return {
    posts,
    totalPosts: allPosts.length,
    filteredPosts: filtered.length,
    pagination: {
      page: safePage,
      page_size: state.pageSize,
      total_pages: totalPages,
      visible_from: posts.length ? offset + 1 : 0,
      visible_to: posts.length ? offset + posts.length : 0,
      has_previous: safePage > 1,
      has_next: totalPages > 0 && safePage < totalPages,
    },
  };
}

function renderSubjectOptions() {
  const rawOptions = Array.isArray(state.archive?.subject_options)
    ? state.archive.subject_options
    : Array.isArray(state.archive?.posts)
      ? state.archive.posts.map((post) => post.subject)
      : [];
  const options = [];
  const seen = new Set();

  for (const rawOption of rawOptions) {
    const value = normalizeSubject(rawOption);
    if (!value || seen.has(value)) {
      continue;
    }
    seen.add(value);
    options.push(value);
  }

  options.sort((left, right) => left.localeCompare(right, "ko-KR"));
  const allOption = document.createElement("option");
  allOption.value = "";
  allOption.textContent = "전체 말머리";
  const subjectOptions = options.slice(0, 100).map(createSubjectOption);

  elements.subjectSelect.replaceChildren(allOption, ...subjectOptions);
  setSubjectControlValue(state.subject);
}

function createSubjectOption(value) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = value;
  return option;
}

function setSubjectControlValue(value) {
  const hasOption = Array.from(elements.subjectSelect.options).some(
    (option) => option.value === value
  );
  if (value && !hasOption) {
    elements.subjectSelect.append(createSubjectOption(value));
  }
  elements.subjectSelect.value = value;
}

function normalizePagination(rawPagination, filteredPosts, visibleCount) {
  const page = normalizePositiveNumber(rawPagination?.page, state.page);
  const pageSize = normalizePositiveNumber(rawPagination?.page_size, state.pageSize);
  const totalPages = normalizeNonNegativeNumber(
    rawPagination?.total_pages,
    filteredPosts === 0 ? 0 : Math.ceil(filteredPosts / pageSize)
  );
  const fallbackFrom = visibleCount ? (page - 1) * pageSize + 1 : 0;

  return {
    page,
    page_size: pageSize,
    total_pages: totalPages,
    visible_from: normalizeNonNegativeNumber(rawPagination?.visible_from, fallbackFrom),
    visible_to: normalizeNonNegativeNumber(
      rawPagination?.visible_to,
      visibleCount ? fallbackFrom + visibleCount - 1 : 0
    ),
    has_previous: Boolean(rawPagination?.has_previous ?? page > 1),
    has_next: Boolean(rawPagination?.has_next ?? (totalPages > 0 && page < totalPages)),
  };
}

function renderSummary(view) {
  const source = state.archive?.source;
  const summary = state.archive?.summary || {};

  if (source) {
    elements.sourceDescription.textContent = `${source.board_name}에서 ${SOURCE_DESCRIPTION}`;
  } else {
    elements.sourceDescription.textContent = state.archive?.error || "대상 게시판 정보를 확인할 수 없습니다.";
  }

  elements.summaryTotal.textContent = numberFormatter.format(view.totalPosts);
  elements.summaryLatest.textContent = formatDateTime(
    summary.latest_seen_at || findLatestSeenAt(state.archive?.posts)
  );

  const runs = Array.isArray(state.archive?.runs) ? state.archive.runs : [];
  elements.runCount.textContent = numberFormatter.format(runs.length);
}

function renderNotice() {
  if (state.dataSource === "fallback") {
    elements.dataNotice.textContent =
      "라이브 데이터 연결에 실패해 로컬 스냅샷을 표시합니다. 최신 상태와 다를 수 있습니다.";
    elements.dataNotice.hidden = false;
    return;
  }

  if (state.dataSource === "unavailable") {
    elements.dataNotice.textContent = state.archive?.error || "데이터를 불러오지 못했습니다.";
    elements.dataNotice.hidden = false;
    return;
  }

  elements.dataNotice.hidden = true;
  elements.dataNotice.textContent = "";
}

function renderRuns() {
  const runs = Array.isArray(state.archive?.runs) ? state.archive.runs : [];
  elements.runs.replaceChildren();

  if (runs.length === 0) {
    const empty = document.createElement("p");
    empty.className = "drawer-empty";
    empty.textContent = "표시할 수집 실행 기록이 없습니다.";
    elements.runs.append(empty);
    return;
  }

  for (const run of runs) {
    const item = document.createElement("article");
    item.className = "run-item";

    const heading = document.createElement("div");
    heading.className = "run-heading";

    const status = document.createElement("strong");
    const statusInfo = getRunStatus(run.status);
    status.className = `run-status ${statusInfo.className}`;
    status.textContent = statusInfo.label;

    const type = document.createElement("span");
    type.textContent = getRunTypeLabel(run.run_type);

    const started = document.createElement("time");
    started.dateTime = String(run.started_at || "");
    started.textContent = formatDateTime(run.started_at);

    heading.append(status, type, started);
    item.append(heading);

    const metrics = document.createElement("dl");
    metrics.className = "run-metrics";
    metrics.append(
      createMetric("페이지", run.scanned_pages),
      createMetric("확인", run.scanned_posts),
      createMetric("저장", run.matched_posts)
    );
    item.append(metrics);

    if (run.error_message) {
      const error = document.createElement("p");
      error.className = "run-error";
      error.textContent = String(run.error_message);
      item.append(error);
    }

    elements.runs.append(item);
  }
}

function createMetric(label, value) {
  const wrapper = document.createElement("div");
  const term = document.createElement("dt");
  const description = document.createElement("dd");
  term.textContent = label;
  description.textContent = numberFormatter.format(normalizeNonNegativeNumber(value, 0));
  wrapper.append(term, description);
  return wrapper;
}

function renderPosts(posts) {
  elements.posts.replaceChildren();

  if (state.dataSource === "unavailable") {
    reserveBoardRows(3);
    renderBoardState("목록을 불러오지 못했습니다.", "다시 시도", loadArchive);
    return;
  }

  if (posts.length === 0) {
    reserveBoardRows(3);
    renderBoardState("현재 조건에 맞는 글이 없습니다.", "필터 초기화", resetFilters);
    return;
  }

  reserveBoardRows(posts.length);

  for (const post of posts) {
    const row = document.createElement("article");
    row.className = "board-row post-row";
    row.setAttribute("role", "row");

    row.append(
      createCell(post.external_post_id || "-", "cell-number numeric-cell"),
      createSubjectCell(post.subject),
      createTitleCell(post),
      createCell(formatPostDate(post.created_at), "cell-date numeric-cell"),
      createCell(numberFormatter.format(normalizeNonNegativeNumber(post.upvotes, 0)), "cell-upvotes numeric-cell"),
      createCell(numberFormatter.format(normalizeNonNegativeNumber(post.comments, 0)), "cell-comments numeric-cell")
    );

    elements.posts.append(row);
  }
}

function createCell(value, className) {
  const cell = document.createElement("span");
  cell.className = `board-cell ${className}`;
  cell.setAttribute("role", "cell");
  cell.textContent = String(value);
  return cell;
}

function createSubjectCell(subject) {
  const value = String(subject || "").trim();
  const preview = createSubjectPreview(value);
  const cell = createCell(preview, "cell-subject");

  if (preview !== value) {
    cell.setAttribute("aria-label", value);
  }

  return cell;
}

function createSubjectPreview(value) {
  const characters = subjectSegmenter
    ? Array.from(subjectSegmenter.segment(value), ({ segment }) => segment)
    : Array.from(value);
  const preview = [];
  let visibleLength = 0;

  for (const character of characters) {
    if (/\s/u.test(character)) {
      if (preview.length && visibleLength < SUBJECT_PREVIEW_LENGTH) {
        preview.push(character);
      }
      continue;
    }
    if (visibleLength >= SUBJECT_PREVIEW_LENGTH) {
      break;
    }
    preview.push(character);
    visibleLength += 1;
  }

  return preview.join("").trimEnd();
}

function createTitleCell(post) {
  const cell = document.createElement("span");
  cell.className = "board-cell cell-title";
  cell.setAttribute("role", "cell");

  const title = String(post.title || "제목 없음");
  const safeUrl = getSafeHttpUrl(post.post_url);
  const content = safeUrl
    ? document.createElement("a")
    : document.createElement("span");
  content.className = "post-title-content";

  if (safeUrl) {
    content.href = safeUrl;
    content.target = "_blank";
    content.rel = "noreferrer noopener";
    content.title = title;
    content.setAttribute("aria-label", `${title} 원문 열기`);
  }

  const titleText = document.createElement("span");
  titleText.className = "post-title-text";
  titleText.textContent = title;
  content.append(titleText);

  cell.append(content);
  return cell;
}

function renderBoardState(message, actionLabel, action) {
  const row = document.createElement("div");
  row.className = "board-state";
  row.setAttribute("role", "row");

  const cell = document.createElement("div");
  cell.className = "board-state-content";
  cell.setAttribute("role", "cell");

  const description = document.createElement("span");
  description.textContent = message;
  cell.append(description);

  if (actionLabel && action) {
    const button = document.createElement("button");
    button.className = "button button-secondary";
    button.type = "button";
    button.textContent = actionLabel;
    button.addEventListener("click", action);
    cell.append(button);
  }

  row.append(cell);
  elements.posts.append(row);
}

function renderResultStatus(view) {
  const filtered = numberFormatter.format(view.filteredPosts);
  const total = numberFormatter.format(view.totalPosts);
  const { visible_from: from, visible_to: to } = view.pagination;

  if (view.filteredPosts === view.totalPosts) {
    elements.resultCount.textContent = `저장된 글 ${total}개`;
  } else {
    elements.resultCount.textContent = `전체 ${total}개 중 조건에 맞는 글 ${filtered}개`;
  }

  elements.rangeSummary.textContent =
    view.filteredPosts === 0 ? "표시할 글이 없습니다." : `${filtered}개 중 ${from}~${to} 표시`;
}

function renderPagination(pagination) {
  elements.pagination.replaceChildren();

  if (pagination.total_pages <= 1) {
    return;
  }

  const pageList = document.createElement("div");
  pageList.className = "pagination-pages";
  pageList.setAttribute("role", "group");
  pageList.setAttribute("aria-label", "페이지 번호");

  elements.pagination.append(createPageJumpForm(pagination.page, pagination.total_pages), pageList);

  for (const entry of getPageSequence(pagination.page, pagination.total_pages)) {
    if (entry === "ellipsis") {
      const ellipsis = document.createElement("span");
      ellipsis.className = "pagination-ellipsis";
      ellipsis.setAttribute("aria-hidden", "true");
      ellipsis.textContent = "…";
      pageList.append(ellipsis);
      continue;
    }

    const button = createPageButton(String(entry), entry, "pagination-page");
    if (entry === pagination.page) {
      button.setAttribute("aria-current", "page");
      button.setAttribute("aria-label", `${entry}페이지, 현재 페이지`);
    } else {
      button.setAttribute("aria-label", `${entry}페이지로 이동`);
    }
    pageList.append(button);
  }

  centerCurrentPage(pageList);
}

function centerCurrentPage(pageList) {
  const currentPage = pageList.querySelector('[aria-current="page"]');
  if (
    !currentPage ||
    typeof window.matchMedia !== "function" ||
    !window.matchMedia("(max-width: 520px)").matches
  ) {
    return;
  }

  window.requestAnimationFrame(() => {
    const listBounds = pageList.getBoundingClientRect();
    const pageBounds = currentPage.getBoundingClientRect();
    pageList.scrollLeft +=
      pageBounds.left - listBounds.left - (listBounds.width - pageBounds.width) / 2;
  });
}

function restorePageChangeFocus() {
  if (!state.focusPageContentAfterLoad) {
    return;
  }

  state.focusPageContentAfterLoad = false;
  elements.archiveTitle.focus({ preventScroll: true });
}

function createPageButton(label, page, className) {
  const button = document.createElement("button");
  button.className = `pagination-button ${className}`;
  button.type = "button";
  button.textContent = label;
  button.addEventListener("click", () => goToPage(page));
  return button;
}

function createPageJumpForm(currentPage, totalPages) {
  const form = document.createElement("form");
  form.className = "pagination-jump";
  form.noValidate = true;
  form.setAttribute("aria-label", "페이지 직접 이동");

  const input = document.createElement("input");
  input.className = "pagination-jump-input";
  input.id = "pagination-jump-input";
  input.name = "page";
  input.type = "number";
  input.inputMode = "numeric";
  input.autocomplete = "off";
  input.min = "1";
  input.max = String(totalPages);
  input.step = "1";
  input.required = true;
  input.value = String(currentPage);
  input.setAttribute(
    "aria-label",
    `이동할 페이지, 1부터 ${numberFormatter.format(totalPages)}까지`
  );

  const submit = document.createElement("button");
  submit.className = "pagination-button pagination-jump-button";
  submit.type = "submit";
  submit.textContent = "가기";

  input.addEventListener("input", () => input.setCustomValidity(""));
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      submit.click();
    }
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const page = parsePageJump(input.value, totalPages);
    if (page === null) {
      input.setCustomValidity(
        `1부터 ${numberFormatter.format(totalPages)} 사이의 정수를 입력하세요.`
      );
      input.reportValidity();
      return;
    }

    input.setCustomValidity("");
    if (page === currentPage) {
      input.focus();
      return;
    }

    goToPage(page);
  });

  form.append(input, submit);
  return form;
}

function getPageSequence(currentPage, totalPages) {
  if (totalPages <= PAGE_WINDOW_RADIUS * 2 + 1) {
    return Array.from({ length: totalPages }, (_, index) => index + 1);
  }

  const windowStart = Math.max(1, currentPage - PAGE_WINDOW_RADIUS);
  const windowEnd = Math.min(totalPages, currentPage + PAGE_WINDOW_RADIUS);
  const pages = new Set([1, totalPages]);

  for (let page = windowStart; page <= windowEnd; page += 1) {
    pages.add(page);
  }

  const sortedPages = Array.from(pages)
    .filter((page) => Number.isInteger(page) && page >= 1 && page <= totalPages)
    .sort((left, right) => left - right);
  const sequence = [];

  for (const page of sortedPages) {
    const previousPage = sequence[sequence.length - 1];
    if (typeof previousPage === "number") {
      const gap = page - previousPage;
      if (gap === 2) {
        sequence.push(previousPage + 1);
      } else if (gap > 2) {
        sequence.push("ellipsis");
      }
    }
    sequence.push(page);
  }

  return sequence;
}

function parsePageJump(value, totalPages) {
  const normalized = String(value ?? "").trim();
  if (!/^\d+$/.test(normalized)) {
    return null;
  }

  const page = Number(normalized);
  return Number.isSafeInteger(page) && page >= 1 && page <= totalPages ? page : null;
}

function goToPage(page) {
  state.page = Math.max(1, page);
  state.focusPageContentAfterLoad = true;
  syncStateToUrl();
  loadArchive();
  elements.archiveTitle.scrollIntoView({ block: "start" });
}

function comparePosts(left, right) {
  if (state.sortBy === "upvotes") {
    return (
      normalizeNonNegativeNumber(right.upvotes, 0) - normalizeNonNegativeNumber(left.upvotes, 0) ||
      compareDate(right.created_at, left.created_at) ||
      compareExternalId(right.external_post_id, left.external_post_id)
    );
  }

  if (state.sortBy === "comments") {
    return (
      normalizeNonNegativeNumber(right.comments, 0) - normalizeNonNegativeNumber(left.comments, 0) ||
      compareDate(right.created_at, left.created_at) ||
      compareExternalId(right.external_post_id, left.external_post_id)
    );
  }

  return (
    compareDate(right.created_at, left.created_at) ||
    compareExternalId(right.external_post_id, left.external_post_id)
  );
}

function compareDate(left, right) {
  return getDateTime(left) - getDateTime(right);
}

function compareExternalId(left, right) {
  return normalizeNonNegativeNumber(left, 0) - normalizeNonNegativeNumber(right, 0);
}

function bindEvents() {
  elements.filterForm.addEventListener("input", scheduleFilterUpdate);
  elements.filterForm.addEventListener("change", scheduleFilterUpdate);
  elements.filterForm.addEventListener("reset", () => {
    window.requestAnimationFrame(resetFilters);
  });

  elements.runsOpen.addEventListener("click", openRunsDrawer);
  elements.runsClose.addEventListener("click", () => elements.runsDrawer.close());
  elements.runsDrawer.addEventListener("click", (event) => {
    if (event.target === elements.runsDrawer) {
      elements.runsDrawer.close();
    }
  });
  elements.runsDrawer.addEventListener("close", () => {
    elements.runsOpen.setAttribute("aria-expanded", "false");
    elements.runsOpen.focus();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && elements.runsDrawer.open) {
      event.preventDefault();
      elements.runsDrawer.close();
    }
  });
}

function scheduleFilterUpdate() {
  window.clearTimeout(state.filterTimer);
  state.filterTimer = window.setTimeout(() => {
    readStateFromControls();
    state.page = 1;
    state.focusPageContentAfterLoad = false;
    syncStateToUrl();
    loadArchive();
  }, 180);
}

function resetFilters() {
  Object.assign(state, DEFAULT_STATE);
  state.focusPageContentAfterLoad = false;
  writeStateToControls();
  syncStateToUrl();
  loadArchive();
}

function openRunsDrawer() {
  if (typeof elements.runsDrawer.showModal === "function") {
    elements.runsDrawer.showModal();
    elements.runsOpen.setAttribute("aria-expanded", "true");
    elements.runsClose.focus();
  }
}

function readStateFromControls() {
  state.search = String(elements.searchInput.value || "").trim().slice(0, 100);
  state.subject = normalizeSubject(elements.subjectSelect.value);
  state.minUpvotes = normalizeNonNegativeNumber(elements.upvotesInput.value, 0);
  state.minComments = normalizeNonNegativeNumber(elements.commentsInput.value, 0);
  state.sortBy = VALID_SORTS.has(elements.sortSelect.value)
    ? elements.sortSelect.value
    : DEFAULT_STATE.sortBy;

  const pageSize = normalizePositiveNumber(elements.pageSizeSelect.value, DEFAULT_STATE.pageSize);
  state.pageSize = VALID_PAGE_SIZES.has(pageSize) ? pageSize : DEFAULT_STATE.pageSize;
}

function writeStateToControls() {
  elements.searchInput.value = state.search;
  setSubjectControlValue(state.subject);
  elements.upvotesInput.value = String(state.minUpvotes);
  elements.commentsInput.value = String(state.minComments);
  elements.sortSelect.value = state.sortBy;
  elements.pageSizeSelect.value = String(state.pageSize);
}

function hydrateStateFromUrl() {
  const params = new URL(window.location.href).searchParams;
  const sortBy = params.get("sort") || DEFAULT_STATE.sortBy;
  const pageSize = normalizePositiveNumber(params.get("page_size"), DEFAULT_STATE.pageSize);
  const minUpvotes = params.get("min_upvotes");

  state.search = String(params.get("q") || "").trim().slice(0, 100);
  state.subject = normalizeSubject(params.get("subject"));
  state.minUpvotes =
    minUpvotes === null
      ? DEFAULT_STATE.minUpvotes
      : normalizeNonNegativeNumber(minUpvotes, DEFAULT_STATE.minUpvotes);
  state.minComments = normalizeNonNegativeNumber(params.get("min_comments"), 0);
  state.sortBy = VALID_SORTS.has(sortBy) ? sortBy : DEFAULT_STATE.sortBy;
  state.page = normalizePositiveNumber(params.get("page"), 1);
  state.pageSize = VALID_PAGE_SIZES.has(pageSize) ? pageSize : DEFAULT_STATE.pageSize;
}

function syncStateToUrl() {
  const url = new URL(window.location.href);
  const values = {
    q: state.search || null,
    subject: state.subject || null,
    min_upvotes:
      state.minUpvotes === DEFAULT_STATE.minUpvotes ? null : state.minUpvotes,
    min_comments: state.minComments || null,
    sort: state.sortBy === DEFAULT_STATE.sortBy ? null : state.sortBy,
    page: state.page === 1 ? null : state.page,
    page_size: state.pageSize === DEFAULT_STATE.pageSize ? null : state.pageSize,
  };

  for (const [key, value] of Object.entries(values)) {
    if (value === null) {
      url.searchParams.delete(key);
    } else {
      url.searchParams.set(key, String(value));
    }
  }

  window.history.replaceState(null, "", url);
}

function renderLoadingState() {
  reserveBoardRows(state.pageSize);
  elements.board.setAttribute("aria-busy", "true");
  elements.resultCount.textContent = "목록을 불러오는 중입니다.";
  elements.rangeSummary.textContent = "표시 범위를 계산하는 중입니다.";
  elements.pagination.replaceChildren();
  elements.posts.replaceChildren();
  renderBoardState("게시글을 불러오는 중입니다.");
}

function reserveBoardRows(count) {
  elements.posts.style.setProperty("--reserved-row-count", String(Math.max(3, count)));
}

function getRunStatus(value) {
  const normalized = String(value || "").toLowerCase();
  if (["success", "completed", "complete"].includes(normalized)) {
    return { label: "성공", className: "status-success" };
  }
  if (["failed", "error"].includes(normalized)) {
    return { label: "실패", className: "status-failed" };
  }
  if (["blocked", "partial"].includes(normalized)) {
    return { label: normalized === "blocked" ? "차단" : "일부 완료", className: "status-warning" };
  }
  if (["running", "started", "in_progress"].includes(normalized)) {
    return { label: "실행 중", className: "status-neutral" };
  }
  return { label: value || "상태 미상", className: "status-neutral" };
}

function getRunTypeLabel(value) {
  const labels = {
    hot: "최신 글",
    hot_scan: "최신 글",
    finalizer: "최종 검사",
    backfill: "과거 백필",
    cycle: "수집 사이클",
    scan: "목록 검사",
  };
  return labels[String(value || "").toLowerCase()] || String(value || "수집 실행");
}

function getSafeHttpUrl(value) {
  try {
    const url = new URL(String(value || ""), window.location.href);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function formatPostDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value || "-");
  }

  const year = String(date.getFullYear()).slice(-2);
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}.${month}.${day}`;
}

function formatDateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "기록 없음" : dateTimeFormatter.format(date);
}

function findLatestSeenAt(posts) {
  if (!Array.isArray(posts) || posts.length === 0) {
    return "";
  }

  return posts.reduce((latest, post) => {
    const candidate = post.last_seen_at || post.created_at || "";
    return getDateTime(candidate) > getDateTime(latest) ? candidate : latest;
  }, "");
}

function getDateTime(value) {
  const timestamp = new Date(value).getTime();
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function normalizeSubject(value) {
  const characters = Array.from(String(value || "").trim());
  return characters.length <= 100 ? characters.join("") : "";
}

function normalizeNonNegativeNumber(value, fallback) {
  const number = Number.parseInt(value, 10);
  return Number.isNaN(number) || number < 0 ? fallback : number;
}

function normalizePositiveNumber(value, fallback) {
  const number = Number.parseInt(value, 10);
  return Number.isNaN(number) || number < 1 ? fallback : number;
}

initialize();
