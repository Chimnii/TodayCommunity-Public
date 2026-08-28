const DEFAULT_TARGET = "dcinside-singularity";
const ALL_TARGET = "all";
const TARGET_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const SOURCE_DESCRIPTION =
  "추천수 또는 댓글수가 일정 조건을 만족하는 글을 모읍니다. 본문 내용은 수집하지 않고 제목과 원문 링크 등 목록 정보만 수집합니다.";
const LIST_ONLY_DESCRIPTION =
  "본문 내용은 수집하지 않고 제목과 원문 링크 등 목록 정보만 수집합니다.";
const ARCHIVE_TAB_LABELS = Object.freeze({
  "dcinside-singularity": "특이점이 온다 갤",
  "dcinside-agent-stack": "AI 활용 갤",
  "fmkorea-munich": "Bayern Munich",
  "game-news": "게임 뉴스",
  all: "모두",
});
const ARCHIVE_MASTHEAD_DESCRIPTIONS = Object.freeze({
  "dcinside-singularity": "디시인사이드 특이점이 온다 갤러리 인기글.",
  "dcinside-agent-stack": "디시인사이드 AI 활용 갤러리 인기글.",
  "fmkorea-munich": "에펨코리아 바이에른 뮌헨 관련 인기글.",
  "game-news": "게임 신작, 인터뷰와 업계 동향을 휴리스틱하게 선별한 기사.",
  all: "모든 공개 아카이브의 글을 최신순으로 모았습니다.",
});
const ARCHIVE_ROW_LABELS = Object.freeze({
  "dcinside-singularity": "특이점",
  "dcinside-agent-stack": "AI활용",
  "fmkorea-munich": "Bayern",
  "game-news": "게임뉴스",
});
const FALLBACK_ARCHIVES = Object.freeze([
  {
    archive_key: "dcinside-singularity",
    display_name: "특이점이 온다",
    description: "디시인사이드 특이점이 온다 갤러리 인기글",
    content_kind: "community",
    display_order: 10,
  },
  {
    archive_key: "dcinside-agent-stack",
    display_name: "AI 활용",
    description: "디시인사이드 AI 활용 갤러리 인기글",
    content_kind: "community",
    display_order: 20,
  },
  {
    archive_key: "fmkorea-munich",
    display_name: "뮌헨",
    description: "에펨코리아의 뮌헨 관련 인기글",
    content_kind: "community",
    display_order: 30,
  },
  {
    archive_key: "game-news",
    display_name: "게임 뉴스",
    description: "게임 신작, 인터뷰와 업계 동향 기사",
    content_kind: "article",
    display_order: 40,
  },
  {
    archive_key: ALL_TARGET,
    display_name: "모두",
    description: "모든 공개 아카이브의 글",
    content_kind: "mixed",
    display_order: 100,
  },
]);
const DEFAULT_STATE = Object.freeze({
  search: "",
  subject: "",
  topicId: 0,
  minUpvotes: 0,
  minComments: 0,
  sortBy: "created_at",
  page: 1,
  pageSize: 30,
});
const VALID_SORTS = new Set(["created_at", "upvotes", "comments"]);
const VALID_PAGE_SIZES = new Set([20, 30, 50, 100]);
const DESKTOP_SUBJECT_PREVIEW_LENGTH = 5;
const MOBILE_SUBJECT_PREVIEW_LENGTH = 5;
const ARTICLE_SOURCE_LABELS = Object.freeze({
  inven: "inv",
  thisisgame: "tig",
});
const ARTICLE_SUBJECT_LABELS = Object.freeze({
  business: "biz",
  development: "dev",
  platform: "store",
  release: "launch",
  technology: "tech",
  other: "etc",
});
const PAGE_WINDOW_RADIUS = 3;
const COMPACT_TOPIC_PANEL_QUERY = "(max-width: 1759px)";
const FEEDBACK_KEY_PATTERN = /^[a-f0-9]{32}$/;
const FEEDBACK_RATINGS = Object.freeze([
  { level: 2, icon: "👍👍", label: "아주 흥미있음" },
  { level: 1, icon: "👍", label: "흥미는 있음" },
  { level: -1, icon: "👎", label: "별로 관심 없음" },
  { level: -2, icon: "👎👎", label: "아주 관심 없음" },
]);
const subjectSegmenter = typeof Intl.Segmenter === "function"
  ? new Intl.Segmenter("ko", { granularity: "grapheme" })
  : null;

const state = {
  ...DEFAULT_STATE,
  target: DEFAULT_TARGET,
  archive: null,
  dataSource: "unknown",
  activeRequest: null,
  filterTimer: null,
  focusPageContentAfterLoad: false,
  focusArchiveTabAfterLoad: false,
  focusTopicAfterLoad: false,
  topicPanelExpanded: true,
  feedbackSession: null,
  feedbackSessionPromise: null,
  feedbackByPost: new Map(),
  feedbackReady: false,
  pendingFeedback: new Set(),
  feedbackDialogPost: null,
  feedbackDialogTrigger: null,
  preferenceDocument: {
    content: "",
    version: 0,
    updatedAt: null,
    maxLength: 1000,
    loaded: false,
  },
  preferenceSaving: false,
  undoAction: null,
  toastTimer: null,
};

const elements = {
  archiveTabs: document.querySelector("#archive-tabs"),
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
  boardHeaderRow: document.querySelector("#board-header-row"),
  numberColumnLabel: document.querySelector("#number-column-label"),
  posts: document.querySelector("#posts"),
  resultCount: document.querySelector("#result-count"),
  rangeSummary: document.querySelector("#range-summary"),
  pagination: document.querySelector("#pagination"),
  dataNotice: document.querySelector("#data-notice"),
  filterShell: document.querySelector("#filter-shell"),
  filterToggle: document.querySelector("#filter-toggle"),
  filterToggleState: document.querySelector(".filter-toggle-state"),
  searchInput: document.querySelector("#search-input"),
  subjectSelect: document.querySelector("#subject-select"),
  subjectFilterLabel: document.querySelector("#subject-filter-label"),
  subjectColumnLabel: document.querySelector("#subject-column-label"),
  sourceColumnLabel: document.querySelector("#source-column-label"),
  titleColumnLabel: document.querySelector("#title-column-label"),
  upvotesColumnLabel: document.querySelector("#upvotes-column-label"),
  dateColumnLabel: document.querySelector("#date-column-label"),
  feedbackColumnLabel: document.querySelector("#feedback-column-label"),
  upvotesInput: document.querySelector("#upvotes-input"),
  commentsInput: document.querySelector("#comments-input"),
  sortSelect: document.querySelector("#sort-select"),
  sortUpvotesOption: document.querySelector('#sort-select option[value="upvotes"]'),
  sortCommentsOption: document.querySelector('#sort-select option[value="comments"]'),
  pageSizeSelect: document.querySelector("#page-size-select"),
  filterForm: document.querySelector("#filter-form"),
  topicPanel: document.querySelector("#topic-panel"),
  topicPanelTitle: document.querySelector("#topic-panel-title"),
  topicPanelToggle: document.querySelector("#topic-panel-toggle"),
  topicPanelToggleState: document.querySelector("#topic-panel-toggle-state"),
  topicPanelContent: document.querySelector("#topic-panel-content"),
  topicClear: document.querySelector("#topic-clear"),
  topicList: document.querySelector("#topic-list"),
  topicEmpty: document.querySelector("#topic-empty"),
  topicPanelMeta: document.querySelector("#topic-panel-meta"),
  gameNewsTools: document.querySelector("#game-news-tools"),
  rulesOpen: document.querySelector("#rules-open"),
  rulesDialog: document.querySelector("#rules-dialog"),
  rulesClose: document.querySelector("#rules-close"),
  preferenceForm: document.querySelector("#preference-form"),
  preferenceDocument: document.querySelector("#preference-document"),
  preferenceUpdated: document.querySelector("#preference-updated"),
  preferenceCount: document.querySelector("#preference-count"),
  preferenceClear: document.querySelector("#preference-clear"),
  preferenceReset: document.querySelector("#preference-reset"),
  preferenceSave: document.querySelector("#preference-save"),
  rulesStatus: document.querySelector("#rules-status"),
  hiddenOpen: document.querySelector("#hidden-open"),
  hiddenCount: document.querySelector("#hidden-count"),
  hiddenDialog: document.querySelector("#hidden-dialog"),
  hiddenClose: document.querySelector("#hidden-close"),
  hiddenStatus: document.querySelector("#hidden-status"),
  hiddenList: document.querySelector("#hidden-list"),
  feedbackDialog: document.querySelector("#feedback-dialog"),
  feedbackDialogClose: document.querySelector("#feedback-dialog-close"),
  feedbackArticleTitle: document.querySelector("#feedback-article-title"),
  feedbackDialogToolbar: document.querySelector("#feedback-dialog-toolbar"),
  feedbackToast: document.querySelector("#feedback-toast"),
  feedbackToastMessage: document.querySelector("#feedback-toast-message"),
  feedbackUndo: document.querySelector("#feedback-undo"),
};

const numberFormatter = new Intl.NumberFormat("ko-KR");
const dateTimeFormatter = new Intl.DateTimeFormat("ko-KR", {
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

async function initialize() {
  hydrateStateFromUrl();
  normalizeContentState();
  applyContentKindMode();
  writeStateToControls();
  setMobileFiltersExpanded(hasActiveFilterState());
  syncTopicPanelForViewport();
  bindEvents();
  try {
    await ensureFeedbackSession();
  } catch {
    state.feedbackSession = {
      authentication: "guest",
      actor: null,
      capabilities: {
        rate: false,
        hide: false,
        manage_rules: false,
        manage_auth: false,
      },
    };
  }
  applyContentKindMode();
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

    state.archive = withArchiveCatalog(payload);
    state.dataSource = "live";

    const responseTarget = normalizeTarget(payload.target);
    if (responseTarget && responseTarget !== state.target) {
      state.target = responseTarget;
      syncStateToUrl();
    }

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
    if (fallback && (!fallback.target || fallback.target === state.target)) {
      state.archive = withArchiveCatalog(fallback);
      state.dataSource = "fallback";
    } else {
      state.archive = {
        target: state.target,
        archives: FALLBACK_ARCHIVES,
        archive: findArchive(FALLBACK_ARCHIVES, state.target),
        sources: [],
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
    target: state.target,
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
  if (state.topicId > 0) {
    params.set("topic", String(state.topicId));
  }

  return `/api/archive?${params.toString()}`;
}

function withArchiveCatalog(payload) {
  const archives = Array.isArray(payload?.archives) && payload.archives.length
    ? payload.archives
    : FALLBACK_ARCHIVES;
  const target = normalizeTarget(payload?.target) || state.target;
  const sources = Array.isArray(payload?.sources)
    ? payload.sources
    : payload?.source
      ? [payload.source]
      : [];

  return {
    ...payload,
    target,
    archives,
    archive: payload?.archive || findArchive(archives, target),
    sources,
    source: payload?.source || sources[0] || null,
  };
}

function getAvailableArchives() {
  const archives = Array.isArray(state.archive?.archives) && state.archive.archives.length
    ? state.archive.archives
    : FALLBACK_ARCHIVES;
  return [...archives].sort((left, right) => {
    const orderDifference =
      normalizeNonNegativeNumber(left?.display_order, 0) -
      normalizeNonNegativeNumber(right?.display_order, 0);
    return orderDifference || String(left?.archive_key || "").localeCompare(
      String(right?.archive_key || "")
    );
  });
}

function getCurrentArchive() {
  const candidate = state.archive?.archive;
  if (candidate?.archive_key === state.target) {
    return candidate;
  }
  return findArchive(getAvailableArchives(), state.target);
}

function isArticleArchive() {
  return (
    state.target === "game-news" ||
    getCurrentArchive()?.content_kind === "article"
  );
}

function isMixedArchive() {
  return state.target === ALL_TARGET || getCurrentArchive()?.content_kind === "mixed";
}

function isGameNewsPost(post) {
  return post?.archive_key === "game-news";
}

function normalizeContentState() {
  if (isMixedArchive()) {
    state.topicId = 0;
    return;
  }
  if (!isArticleArchive()) {
    return;
  }
  state.minUpvotes = 0;
  state.minComments = 0;
  state.sortBy = "created_at";
  state.topicId = 0;
}

function applyContentKindMode() {
  const articleMode = isArticleArchive();
  const mixedMode = isMixedArchive();
  const authentication = ["authenticated", "admin"].includes(
    state.feedbackSession?.authentication
  )
    ? state.feedbackSession.authentication
    : "guest";
  const canUseFeedback = Boolean(state.feedbackSession?.capabilities?.rate);
  document.body.dataset.contentKind = mixedMode
    ? "mixed"
    : articleMode
      ? "article"
      : "community";
  document.body.dataset.authState = authentication;
  elements.board.setAttribute(
    "aria-label",
    mixedMode
      ? "모든 아카이브의 저장 글"
      : articleMode
        ? "저장된 게임 기사"
        : "저장된 커뮤니티 글"
  );
  elements.gameNewsTools.hidden = !articleMode || !canUseFeedback;

  if (elements.subjectFilterLabel) {
    elements.subjectFilterLabel.textContent = mixedMode
      ? "말머리/주제"
      : articleMode
        ? "주제"
        : "말머리";
  }
  if (elements.subjectColumnLabel) {
    elements.subjectColumnLabel.textContent = mixedMode
      ? "분류"
      : articleMode
        ? "주제"
        : "말머리";
  }
  if (elements.sourceColumnLabel) {
    elements.sourceColumnLabel.textContent = mixedMode ? "소속" : "출처";
  }
  for (const option of [elements.sortUpvotesOption, elements.sortCommentsOption]) {
    if (option) {
      option.hidden = articleMode;
      option.disabled = articleMode;
    }
  }
  if (articleMode) {
    elements.upvotesInput.value = "0";
    elements.commentsInput.value = "0";
    elements.sortSelect.value = "created_at";
  }
  renderColumnHeaders(mixedMode);
}

function renderColumnHeaders(mixedMode) {
  if (!elements.boardHeaderRow) {
    return;
  }
  const orderedHeaders = mixedMode
    ? [
        elements.numberColumnLabel,
        elements.sourceColumnLabel,
        elements.subjectColumnLabel,
        elements.titleColumnLabel,
        elements.upvotesColumnLabel,
        elements.dateColumnLabel,
        elements.feedbackColumnLabel,
      ]
    : [
        elements.numberColumnLabel,
        elements.subjectColumnLabel,
        elements.sourceColumnLabel,
        elements.titleColumnLabel,
        elements.upvotesColumnLabel,
        elements.dateColumnLabel,
        elements.feedbackColumnLabel,
      ];
  elements.boardHeaderRow.append(...orderedHeaders.filter(Boolean));
}

function getCurrentSources() {
  return Array.isArray(state.archive?.sources) ? state.archive.sources : [];
}

function findArchive(archives, target) {
  return Array.isArray(archives)
    ? archives.find((archive) => archive?.archive_key === target) || null
    : null;
}

function renderArchiveTabs() {
  const tabs = getAvailableArchives().map((archive) => {
    const key = normalizeTarget(archive?.archive_key);
    if (!key) {
      return null;
    }

    const tab = document.createElement("a");
    tab.className = "archive-tab";
    tab.setAttribute("role", "tab");
    tab.href = buildArchiveHref(key);
    tab.textContent = ARCHIVE_TAB_LABELS[key] || String(archive.display_name || key);
    tab.setAttribute("aria-controls", "archive-board");
    tab.setAttribute("aria-selected", String(key === state.target));
    tab.tabIndex = key === state.target ? 0 : -1;
    tab.addEventListener("click", (event) => {
      event.preventDefault();
      selectArchive(key);
    });
    tab.addEventListener("keydown", handleArchiveTabKeydown);
    return tab;
  }).filter(Boolean);

  elements.archiveTabs.replaceChildren(...tabs);
  keepSelectedArchiveTabVisible();
}

function keepSelectedArchiveTabVisible() {
  const navigation = elements.archiveTabs.parentElement;
  const selected = elements.archiveTabs.querySelector('[role="tab"][aria-selected="true"]');
  if (!navigation || !selected) {
    return;
  }

  const visibleStart = navigation.scrollLeft;
  const visibleEnd = visibleStart + navigation.clientWidth;
  const selectedStart = selected.offsetLeft;
  const selectedEnd = selectedStart + selected.offsetWidth;
  if (selectedStart < visibleStart) {
    navigation.scrollLeft = selectedStart;
  } else if (selectedEnd > visibleEnd) {
    navigation.scrollLeft = selectedEnd - navigation.clientWidth;
  }
}

function buildArchiveHref(target) {
  const url = new URL(window.location.href);
  url.search = "";
  url.searchParams.set("target", target);
  return `${url.pathname}${url.search}${url.hash}`;
}

function handleArchiveTabKeydown(event) {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
    return;
  }

  const tabs = Array.from(elements.archiveTabs.querySelectorAll('[role="tab"]'));
  const currentIndex = tabs.indexOf(event.currentTarget);
  if (currentIndex < 0 || tabs.length === 0) {
    return;
  }

  event.preventDefault();
  let nextIndex;
  if (event.key === "Home") {
    nextIndex = 0;
  } else if (event.key === "End") {
    nextIndex = tabs.length - 1;
  } else {
    const direction = event.key === "ArrowRight" ? 1 : -1;
    nextIndex = (currentIndex + direction + tabs.length) % tabs.length;
  }

  tabs[nextIndex].focus();
  tabs[nextIndex].click();
}

function selectArchive(target) {
  const normalizedTarget = normalizeTarget(target);
  if (!normalizedTarget || normalizedTarget === state.target) {
    return;
  }

  window.clearTimeout(state.filterTimer);
  Object.assign(state, DEFAULT_STATE);
  state.target = normalizedTarget;
  state.feedbackByPost.clear();
  state.feedbackReady = false;
  state.focusPageContentAfterLoad = false;
  state.focusArchiveTabAfterLoad = true;
  state.focusTopicAfterLoad = false;
  writeStateToControls();
  syncStateToUrl({ replace: false });
  renderArchiveTabs();
  loadArchive();
}

function render() {
  normalizeContentState();
  applyContentKindMode();
  const view = getViewModel();
  elements.board.setAttribute("aria-busy", "false");
  renderArchiveTabs();
  renderSubjectOptions();
  renderSummary(view);
  renderTopicPanel();
  renderNotice();
  renderRuns();
  renderPosts(view.posts);
  if (isArticleArchive() && state.feedbackSession?.capabilities?.rate) {
    void loadFeedbackState(view.posts);
  }
  renderResultStatus(view);
  renderPagination(view.pagination);
  restoreArchiveTabFocus();
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
        (state.minUpvotes > 0 &&
          normalizeSignedInteger(post.upvotes, 0) < state.minUpvotes) ||
        (state.minComments > 0 &&
          normalizeNonNegativeNumber(post.comments, 0) < state.minComments)
      ) {
        return false;
      }

      if (state.subject && normalizeSubject(post.subject) !== state.subject) {
        return false;
      }

      if (
        state.topicId > 0 &&
        !(
          Array.isArray(post.topic_ids) &&
          post.topic_ids.some((topicId) => Number(topicId) === state.topicId)
        )
      ) {
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
  allOption.textContent = isMixedArchive()
    ? "전체 말머리/주제"
    : isArticleArchive()
      ? "전체 주제"
      : "전체 말머리";
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
  const archive = getCurrentArchive();
  const sources = getCurrentSources();
  const source = sources[0] || state.archive?.source;
  const summary = state.archive?.summary || {};

  if (archive) {
    const descriptionLead =
      ARCHIVE_MASTHEAD_DESCRIPTIONS[archive.archive_key] || `${archive.description}.`;
    elements.sourceDescription.replaceChildren(
      descriptionLead,
      document.createElement("br"),
      LIST_ONLY_DESCRIPTION
    );
    elements.archiveTitle.textContent = `${archive.display_name} 아카이브`;
    elements.board.setAttribute("aria-label", `${archive.display_name} 저장 글`);
    document.title = `${archive.display_name} | 오늘의 커뮤니티`;
  } else if (source) {
    elements.sourceDescription.textContent = `${source.board_name}에서 ${SOURCE_DESCRIPTION}`;
    elements.archiveTitle.textContent = "선별 글 아카이브";
    elements.board.setAttribute("aria-label", "저장된 커뮤니티 글");
    document.title = "오늘의 커뮤니티 | 선별 글 아카이브";
  } else {
    elements.sourceDescription.textContent = state.archive?.error || "대상 게시판 정보를 확인할 수 없습니다.";
    elements.archiveTitle.textContent = "선별 글 아카이브";
    elements.board.setAttribute("aria-label", "저장된 커뮤니티 글");
    document.title = "오늘의 커뮤니티 | 선별 글 아카이브";
  }

  elements.summaryTotal.textContent = numberFormatter.format(view.totalPosts);
  elements.summaryLatest.textContent = formatDateTime(
    summary.latest_seen_at || findLatestSeenAt(state.archive?.posts)
  );

  const runs = Array.isArray(state.archive?.runs) ? state.archive.runs : [];
  elements.runCount.textContent = numberFormatter.format(runs.length);
}

function renderTopicPanel() {
  const communityArchive = !isArticleArchive() && !isMixedArchive();
  elements.topicPanel.hidden = !communityArchive;
  if (!communityArchive) {
    elements.topicList.replaceChildren();
    return;
  }

  const trends = state.archive?.topic_trends;
  const topics = Array.isArray(trends?.topics) ? trends.topics : [];
  const selectedTopic = state.archive?.selected_topic;
  const selectedTopicId = normalizePositiveNumber(selectedTopic?.topic_id, 0);
  const selectedLabel = selectedTopicId === state.topicId
    ? String(selectedTopic?.label || "").trim()
    : "";

  elements.topicClear.hidden = state.topicId === 0;
  elements.topicClear.textContent = selectedLabel
    ? `‘${selectedLabel}’ 필터 해제`
    : "전체 글 보기";
  elements.topicList.replaceChildren();

  for (const topic of topics) {
    const topicId = normalizePositiveNumber(topic?.topic_id, 0);
    const label = String(topic?.label || "").trim();
    if (!topicId || !label) {
      continue;
    }
    const count = normalizeNonNegativeNumber(topic?.post_count, 0);
    const entry = document.createElement("article");
    entry.className = "topic-entry";

    const button = document.createElement("button");
    button.className = "topic-item";
    button.type = "button";
    button.dataset.topicId = String(topicId);
    button.setAttribute("aria-pressed", String(topicId === state.topicId));
    button.setAttribute(
      "aria-label",
      `${label}, ${numberFormatter.format(count)}개 글`
    );

    const labelElement = document.createElement("span");
    labelElement.className = "topic-label";
    labelElement.textContent = label;
    const countElement = document.createElement("span");
    countElement.className = "topic-count";
    countElement.textContent = `(${numberFormatter.format(count)}개)`;
    button.append(labelElement, countElement);
    button.addEventListener("click", () => {
      applyTopicFilter(topicId === state.topicId ? 0 : topicId);
    });
    entry.append(button);
    elements.topicList.append(entry);
  }

  const hasTopics = elements.topicList.childElementCount > 0;
  elements.topicEmpty.hidden = hasTopics;
  elements.topicEmpty.textContent = trends
    ? "반복해서 다뤄진 토픽이 아직 없습니다."
    : state.dataSource === "fallback"
      ? "로컬 스냅샷에는 토픽 정보가 없습니다."
      : "첫 토픽 분석이 완료되면 여기에 표시됩니다.";

  if (trends) {
    const windowHours = normalizeNonNegativeNumber(trends.window_hours, 0);
    elements.topicPanelMeta.textContent =
      `${numberFormatter.format(windowHours)}시간 기준 · ` +
      `${formatDateTime(trends.generated_at)} 갱신`;
  } else {
    elements.topicPanelMeta.textContent = "제목·말머리 기준";
  }

  if (state.topicId > 0 && isCompactTopicPanel()) {
    setTopicPanelExpanded(true);
  }
  restoreTopicFocus();
}

function applyTopicFilter(topicId) {
  const normalized = normalizePositiveNumber(topicId, 0);
  if (normalized === state.topicId) {
    return;
  }
  window.clearTimeout(state.filterTimer);
  state.topicId = normalized;
  state.page = 1;
  state.focusPageContentAfterLoad = false;
  state.focusTopicAfterLoad = true;
  syncStateToUrl({ replace: false });
  loadArchive();
}

function restoreTopicFocus() {
  if (!state.focusTopicAfterLoad) {
    return;
  }
  state.focusTopicAfterLoad = false;
  window.requestAnimationFrame(() => {
    const selectedButton = state.topicId > 0
      ? elements.topicList.querySelector(`[data-topic-id="${state.topicId}"]`)
      : null;
    const target = selectedButton ||
      (state.topicId > 0 && !elements.topicClear.hidden
        ? elements.topicClear
        : elements.topicPanelTitle);
    target?.focus({ preventScroll: true });
  });
}

function isCompactTopicPanel() {
  return typeof window.matchMedia === "function"
    ? window.matchMedia(COMPACT_TOPIC_PANEL_QUERY).matches
    : false;
}

function setTopicPanelExpanded(expanded) {
  state.topicPanelExpanded = Boolean(expanded);
  elements.topicPanelToggle.setAttribute(
    "aria-expanded",
    String(state.topicPanelExpanded)
  );
  elements.topicPanelToggleState.textContent = state.topicPanelExpanded
    ? "접기"
    : "펼치기";
  elements.topicPanelContent.hidden = !state.topicPanelExpanded;
}

function syncTopicPanelForViewport() {
  if (!isCompactTopicPanel()) {
    setTopicPanelExpanded(true);
    return;
  }
  setTopicPanelExpanded(state.topicId > 0);
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
    const runType = getRunTypeLabel(run.run_type);
    type.textContent = run.board_name ? `${run.board_name} · ${runType}` : runType;

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

    const numberCell = createCell(
      post.external_post_id || "-",
      "cell-number numeric-cell"
    );
    const sourceCell = createSourceCell(post);
    const subjectCell = createSubjectCell(post);
    const titleCell = createTitleCell(post);
    const upvotesCell = createCell(
      isMixedArchive() && isGameNewsPost(post)
        ? "-"
        : numberFormatter.format(normalizeSignedInteger(post.upvotes, 0)),
      "cell-upvotes numeric-cell"
    );
    const dateCell = createCell(
      formatPostDate(post.created_at),
      "cell-date numeric-cell"
    );
    const feedbackCell = createFeedbackCell(post);
    row.append(
      ...(isMixedArchive()
        ? [
            numberCell,
            sourceCell,
            subjectCell,
            titleCell,
            upvotesCell,
            dateCell,
            feedbackCell,
          ]
        : [
            numberCell,
            subjectCell,
            sourceCell,
            titleCell,
            upvotesCell,
            dateCell,
            feedbackCell,
          ])
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

function createSubjectCell(post) {
  const value = String(post?.subject || "").trim();
  const cell = createCell("", "cell-subject");

  if (isMixedArchive() && isGameNewsPost(post)) {
    const sources = getCurrentSources();
    const sourceLabel = getArticleSourceLabel(post, sources) || "-";
    const subjectLabel = getArticleSubjectLabel(value) || "-";
    cell.textContent = `${sourceLabel}-${subjectLabel}`;
    cell.setAttribute(
      "aria-label",
      `출처 ${getArticleSourceFullName(post, sources) || sourceLabel}, 주제 ${value || "없음"}`
    );
    return cell;
  }

  if (!value) {
    return cell;
  }

  if (isArticleArchive()) {
    const label = getArticleSubjectLabel(value);
    cell.textContent = label;
    if (label !== value) {
      cell.setAttribute("aria-label", `주제 ${value}`);
    }
    return cell;
  }

  const desktopPreview = createSubjectPreview(value, DESKTOP_SUBJECT_PREVIEW_LENGTH);
  const mobilePreview = createSubjectPreview(value, MOBILE_SUBJECT_PREVIEW_LENGTH);
  const desktopContent = document.createElement("span");
  const mobileContent = document.createElement("span");
  desktopContent.className = "subject-preview-desktop";
  mobileContent.className = "subject-preview-mobile";
  desktopContent.textContent = desktopPreview;
  mobileContent.textContent = mobilePreview;
  cell.append(desktopContent, mobileContent);

  if (desktopPreview !== value || mobilePreview !== value) {
    cell.setAttribute("aria-label", value);
  }

  return cell;
}

function createFeedbackCell(post) {
  const cell = document.createElement("span");
  cell.className = "board-cell cell-feedback";
  cell.setAttribute("role", "cell");

  if (!isArticleArchive() || !state.feedbackSession?.capabilities?.rate) {
    return cell;
  }

  const postKey = normalizeFeedbackKey(post?.feedback_key);
  if (!postKey) {
    const unavailable = document.createElement("span");
    unavailable.className = "visually-hidden";
    unavailable.textContent = "이 글은 아직 평가할 수 없습니다.";
    cell.append(unavailable);
    return cell;
  }

  const button = document.createElement("button");
  button.className = "feedback-open-button";
  button.type = "button";
  button.dataset.postKey = postKey;
  button.textContent = "평가";
  button.addEventListener("click", () => openFeedbackDialog(post, button));
  cell.append(button);
  syncFeedbackOpenButton(button);
  return cell;
}

function openFeedbackDialog(post, trigger) {
  const postKey = normalizeFeedbackKey(post?.feedback_key);
  if (!postKey || typeof elements.feedbackDialog.showModal !== "function") {
    return;
  }
  const title = String(post?.title || "게임 기사");
  state.feedbackDialogPost = post;
  state.feedbackDialogTrigger = trigger;
  elements.feedbackArticleTitle.textContent = title;
  elements.feedbackDialogToolbar.dataset.postKey = postKey;
  elements.feedbackDialogToolbar.setAttribute("aria-label", `「${title}」 평가`);
  elements.feedbackDialogToolbar.replaceChildren();

  for (const rating of FEEDBACK_RATINGS) {
    const button = document.createElement("button");
    button.className = "feedback-button";
    button.type = "button";
    button.dataset.rating = String(rating.level);
    button.dataset.feedbackLabel = rating.label;
    button.setAttribute("aria-label", rating.label);
    button.setAttribute("aria-pressed", "false");
    button.textContent = rating.icon;
    button.addEventListener("click", () => {
      void submitRating(postKey, rating.level, elements.feedbackDialogToolbar, button);
    });
    elements.feedbackDialogToolbar.append(button);
  }

  const hideButton = document.createElement("button");
  hideButton.className = "feedback-button feedback-hide";
  hideButton.type = "button";
  hideButton.dataset.action = "hide";
  hideButton.setAttribute("aria-label", "목록에서 숨기고 강한 비선호로 기록");
  hideButton.textContent = "×";
  hideButton.addEventListener("click", () => {
    void hidePost(post, elements.feedbackDialogToolbar, hideButton);
  });
  elements.feedbackDialogToolbar.append(hideButton);

  elements.feedbackDialog.showModal();
  syncFeedbackToolbar(elements.feedbackDialogToolbar);
  window.requestAnimationFrame(() => {
    const selected = elements.feedbackDialogToolbar.querySelector(
      '.feedback-button[aria-pressed="true"]'
    );
    (selected || elements.feedbackDialogToolbar.querySelector("button"))?.focus();
  });
}

function normalizeFeedbackKey(value) {
  const key = String(value || "").trim().toLocaleLowerCase("en-US");
  return FEEDBACK_KEY_PATTERN.test(key) ? key : "";
}

async function loadFeedbackState(posts) {
  const session = await ensureFeedbackSession();
  if (!session?.capabilities?.rate) {
    state.feedbackByPost.clear();
    state.feedbackReady = false;
    syncRenderedFeedbackControls();
    return;
  }
  const postKeys = [...new Set(
    posts.map((post) => normalizeFeedbackKey(post?.feedback_key)).filter(Boolean)
  )];
  if (!postKeys.length) {
    state.feedbackReady = true;
    syncRenderedFeedbackControls();
    return;
  }

  try {
    const params = new URLSearchParams();
    for (const postKey of postKeys) {
      params.append("post_key", postKey);
    }
    const payload = await fetchGameNewsJson(`/api/game-news/feedback?${params}`);
    for (const item of Array.isArray(payload?.items) ? payload.items : []) {
      const postKey = normalizeFeedbackKey(item?.post_key);
      if (postKey) {
        state.feedbackByPost.set(postKey, normalizeFeedbackState(item));
      }
    }
    state.feedbackReady = true;
    syncRenderedFeedbackControls();
    void refreshHiddenCount();
  } catch (error) {
    state.feedbackReady = false;
    syncRenderedFeedbackControls();
    showFeedbackToast(`평가 기록을 불러오지 못했습니다: ${error.message}`);
  }
}

async function ensureFeedbackSession() {
  if (state.feedbackSession) {
    return state.feedbackSession;
  }
  if (!state.feedbackSessionPromise) {
    state.feedbackSessionPromise = fetchGameNewsJson("/api/game-news/session")
      .then((session) => {
        state.feedbackSession = session;
        return session;
      })
      .finally(() => {
        state.feedbackSessionPromise = null;
      });
  }
  return state.feedbackSessionPromise;
}

function normalizeFeedbackState(item) {
  const rating = Number(item?.rating_level);
  return {
    post_key: normalizeFeedbackKey(item?.post_key),
    rating_level: FEEDBACK_RATINGS.some((entry) => entry.level === rating)
      ? rating
      : null,
    feedback_version: normalizeNonNegativeNumber(item?.feedback_version, 0),
    reason_code: item?.reason_code ? String(item.reason_code) : null,
    hidden: Boolean(item?.hidden),
  };
}

function syncRenderedFeedbackControls() {
  for (const button of elements.posts.querySelectorAll(".feedback-open-button")) {
    syncFeedbackOpenButton(button);
  }
  if (elements.feedbackDialog.open) {
    syncFeedbackToolbar(elements.feedbackDialogToolbar);
  }
}

function syncFeedbackOpenButton(button) {
  const postKey = normalizeFeedbackKey(button?.dataset?.postKey);
  const feedback = state.feedbackByPost.get(postKey) || { rating_level: null };
  const rating = FEEDBACK_RATINGS.find((entry) => entry.level === feedback.rating_level);
  const pending = state.pendingFeedback.has(postKey);
  button.classList.toggle("is-rated", Boolean(rating));
  button.disabled = !state.feedbackReady || pending;
  button.setAttribute(
    "aria-label",
    rating ? `평가 열기, 현재 ${rating.label}` : "평가 열기"
  );
  button.title = button.disabled && !pending
    ? "평가 기록을 확인한 뒤 사용할 수 있습니다."
    : button.getAttribute("aria-label");
}

function syncFeedbackToolbar(toolbar) {
  const postKey = normalizeFeedbackKey(toolbar?.dataset?.postKey);
  const feedback = state.feedbackByPost.get(postKey) || { rating_level: null };
  const pending = state.pendingFeedback.has(postKey);
  const disabled = !state.feedbackReady || pending;
  const buttons = Array.from(toolbar.querySelectorAll("button"));
  let rovingIndex = 0;

  for (const [index, button] of buttons.entries()) {
    const rating = Number(button.dataset.rating);
    const selected = Number.isInteger(rating) && rating === feedback.rating_level;
    if (button.hasAttribute("aria-pressed")) {
      button.setAttribute("aria-pressed", String(selected));
      const label = button.dataset.feedbackLabel || "평가";
      button.setAttribute(
        "aria-label",
        selected ? `${label}, 선택됨, 다시 누르면 평가 지우기` : label
      );
    }
    if (selected) {
      rovingIndex = index;
    }
    button.disabled = disabled;
    button.title = disabled && !pending
      ? "평가 기록을 확인한 뒤 사용할 수 있습니다."
      : button.getAttribute("aria-label");
  }
  buttons.forEach((button, index) => {
    button.tabIndex = index === rovingIndex ? 0 : -1;
  });
  toolbar.setAttribute("aria-busy", String(pending));
}

function handleFeedbackToolbarKeydown(event) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
    return;
  }
  const buttons = Array.from(event.currentTarget.querySelectorAll("button:not(:disabled)"));
  if (!buttons.length) {
    return;
  }
  event.preventDefault();
  const currentIndex = Math.max(0, buttons.indexOf(document.activeElement));
  let nextIndex = currentIndex;
  if (event.key === "Home") {
    nextIndex = 0;
  } else if (event.key === "End") {
    nextIndex = buttons.length - 1;
  } else {
    const offset = event.key === "ArrowRight" ? 1 : -1;
    nextIndex = (currentIndex + offset + buttons.length) % buttons.length;
  }
  buttons.forEach((button, index) => {
    button.tabIndex = index === nextIndex ? 0 : -1;
  });
  buttons[nextIndex].focus();
}

async function submitRating(postKey, ratingLevel, toolbar, sourceButton) {
  if (state.pendingFeedback.has(postKey)) {
    return;
  }
  const current = state.feedbackByPost.get(postKey)?.rating_level ?? null;
  const nextRating = current === ratingLevel ? null : ratingLevel;
  state.pendingFeedback.add(postKey);
  syncFeedbackToolbar(toolbar);
  let saved = false;
  try {
    const payload = await postGameNewsJson("/api/game-news/feedback", {
      post_key: postKey,
      rating_level: nextRating,
      reason_code: null,
      idempotency_key: createRequestKey("feedback"),
    });
    state.feedbackByPost.set(postKey, normalizeFeedbackState(payload.item));
    const label = nextRating === null
      ? "평가를 지웠습니다."
      : `${FEEDBACK_RATINGS.find((item) => item.level === nextRating)?.label || "평가"}으로 기록했습니다.`;
    showFeedbackToast(label);
    saved = true;
  } catch (error) {
    showFeedbackToast(`평가를 저장하지 못했습니다: ${error.message}`);
  } finally {
    state.pendingFeedback.delete(postKey);
    syncRenderedFeedbackControls();
    if (saved && elements.feedbackDialog.open) {
      elements.feedbackDialog.close();
    } else if (sourceButton.isConnected) {
      sourceButton.focus({ preventScroll: true });
    }
  }
}

async function hidePost(post, toolbar, sourceButton) {
  const postKey = normalizeFeedbackKey(post?.feedback_key);
  if (!postKey || state.pendingFeedback.has(postKey)) {
    return;
  }
  state.pendingFeedback.add(postKey);
  syncFeedbackToolbar(toolbar);
  try {
    await postGameNewsJson("/api/game-news/visibility", {
      post_key: postKey,
      action: "hide",
      idempotency_key: createRequestKey("hide"),
    });
    const title = String(post?.title || "게임 기사");
    const triggerButtons = Array.from(
      elements.posts.querySelectorAll(".feedback-open-button")
    );
    const triggerIndex = Math.max(0, triggerButtons.indexOf(state.feedbackDialogTrigger));
    state.feedbackDialogTrigger = null;
    if (elements.feedbackDialog.open) {
      elements.feedbackDialog.close();
    }
    state.archive.posts = (state.archive.posts || []).filter(
      (item) => normalizeFeedbackKey(item?.feedback_key) !== postKey
    );
    renderPosts(getViewModel().posts);
    window.requestAnimationFrame(() => {
      const remaining = Array.from(
        elements.posts.querySelectorAll(".feedback-open-button:not(:disabled)")
      );
      remaining[Math.min(triggerIndex, Math.max(0, remaining.length - 1))]
        ?.focus({ preventScroll: true });
    });
    showFeedbackToast(`「${title}」을 숨겼습니다.`, async () => {
      await restoreHiddenPost(postKey);
    });
    void refreshHiddenCount();
    void loadArchive();
  } catch (error) {
    showFeedbackToast(`글을 숨기지 못했습니다: ${error.message}`);
    if (sourceButton.isConnected) {
      sourceButton.focus({ preventScroll: true });
    }
  } finally {
    state.pendingFeedback.delete(postKey);
    syncRenderedFeedbackControls();
  }
}

async function restoreHiddenPost(postKey) {
  await postGameNewsJson("/api/game-news/visibility", {
    post_key: postKey,
    action: "restore",
    idempotency_key: createRequestKey("restore"),
  });
  showFeedbackToast("숨긴 글을 목록에 복구했습니다.");
  await loadArchive();
  void refreshHiddenCount();
}

async function fetchGameNewsJson(url, options = {}) {
  const { headers = {}, ...requestOptions } = options;
  const response = await fetch(url, {
    cache: "no-store",
    ...requestOptions,
    headers: { accept: "application/json", ...headers },
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const error = new Error(payload?.error || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

function postGameNewsJson(url, body) {
  return fetchGameNewsJson(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-TodayCommunity-Write": "1",
    },
    body: JSON.stringify(body),
  });
}

function createRequestKey(prefix) {
  const random = globalThis.crypto?.randomUUID?.()
    || `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 14)}`;
  return `${prefix}:${random}`;
}

function showFeedbackToast(message, undoAction = null) {
  window.clearTimeout(state.toastTimer);
  state.undoAction = undoAction;
  elements.feedbackToastMessage.textContent = String(message);
  elements.feedbackUndo.hidden = typeof undoAction !== "function";
  elements.feedbackToast.hidden = false;
  state.toastTimer = window.setTimeout(() => {
    state.undoAction = null;
    elements.feedbackToast.hidden = true;
  }, undoAction ? 10000 : 5000);
}

function createSourceCell(post) {
  const sources = getCurrentSources();
  if (isMixedArchive()) {
    const archive = findArchive(getAvailableArchives(), post?.archive_key);
    const label = ARCHIVE_ROW_LABELS[post?.archive_key]
      || String(archive?.display_name || post?.archive_key || "-");
    const cell = createCell(label, "cell-source");
    const fullName = String(archive?.display_name || "").trim();
    if (fullName && fullName !== label) {
      cell.setAttribute("aria-label", `소속 ${fullName}`);
    }
    return cell;
  }

  const label = getArticleSourceLabel(post, sources);
  const cell = createCell(label || "-", "cell-source");
  const fullName = getArticleSourceFullName(post, sources);

  if (fullName && fullName.toLocaleLowerCase("ko-KR") !== label) {
    cell.setAttribute("aria-label", `출처 ${fullName}`);
  }

  return cell;
}

function getArticleSourceFullName(post, sources = []) {
  const source = Array.isArray(sources)
    ? sources.find((candidate) => candidate?.source_key === post?.source_key)
    : null;
  return String(source?.board_name || source?.site_name || "").trim();
}

function getArticleSourceLabel(post, sources = []) {
  const source = Array.isArray(sources)
    ? sources.find((candidate) => candidate?.source_key === post?.source_key)
    : null;
  const siteName = normalizeSiteIdentifier(source?.site_name);

  if (siteName) {
    return (ARTICLE_SOURCE_LABELS[siteName] || siteName).slice(0, 3);
  }

  const sourceKey = String(post?.source_key || "").trim();
  return normalizeSiteIdentifier(sourceKey.replace(/^game-news-/i, "")).slice(0, 3);
}

function getArticleSubjectLabel(subject) {
  const value = String(subject || "").trim();
  return ARTICLE_SUBJECT_LABELS[value.toLocaleLowerCase("en-US")] || value;
}

function normalizeSiteIdentifier(value) {
  const normalized = String(value || "")
    .trim()
    .toLocaleLowerCase("en-US")
    .replace(/^https?:\/\//, "")
    .replace(/^www\./, "");
  return normalized.split(/[./]/u, 1)[0].replace(/[^a-z0-9-]/gu, "");
}

function createSubjectPreview(value, previewLength) {
  const characters = subjectSegmenter
    ? Array.from(subjectSegmenter.segment(value), ({ segment }) => segment)
    : splitSubjectGraphemes(value);
  const preview = [];
  let visibleLength = 0;

  for (const character of characters) {
    if (/\s/u.test(character)) {
      if (preview.length && visibleLength < previewLength) {
        preview.push(character);
      }
      continue;
    }
    if (visibleLength >= previewLength) {
      break;
    }
    preview.push(character);
    visibleLength += 1;
  }

  return preview.join("").trimEnd();
}

function splitSubjectGraphemes(value) {
  const graphemes = [];
  let joinNext = false;

  for (const character of Array.from(value)) {
    const previous = graphemes[graphemes.length - 1];
    if (!previous) {
      graphemes.push(character);
      joinNext = character === "\u200d";
      continue;
    }

    if (joinNext || isGraphemeExtension(character)) {
      graphemes[graphemes.length - 1] += character;
      joinNext = character === "\u200d";
      continue;
    }

    if (isRegionalIndicator(character) && isUnpairedRegionalIndicator(previous)) {
      graphemes[graphemes.length - 1] += character;
      continue;
    }

    graphemes.push(character);
    joinNext = character === "\u200d";
  }

  return graphemes;
}

function isGraphemeExtension(character) {
  const codePoint = character.codePointAt(0);
  return (
    character === "\u200d" ||
    /\p{Mark}/u.test(character) ||
    (codePoint >= 0xfe00 && codePoint <= 0xfe0f) ||
    (codePoint >= 0x1f3fb && codePoint <= 0x1f3ff) ||
    (codePoint >= 0xe0020 && codePoint <= 0xe007f) ||
    (codePoint >= 0xe0100 && codePoint <= 0xe01ef)
  );
}

function isRegionalIndicator(character) {
  const codePoint = character.codePointAt(0);
  return codePoint >= 0x1f1e6 && codePoint <= 0x1f1ff;
}

function isUnpairedRegionalIndicator(grapheme) {
  const characters = Array.from(grapheme);
  return characters.length % 2 === 1 && characters.every(isRegionalIndicator);
}

function createTitleCell(post) {
  const cell = document.createElement("span");
  cell.className = "board-cell cell-title";
  cell.setAttribute("role", "cell");

  const title = String(post.title || "제목 없음");
  const comments = normalizeNonNegativeNumber(post.comments, 0);
  const articleMode = isArticleArchive() || (isMixedArchive() && isGameNewsPost(post));
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
    content.setAttribute(
      "aria-label",
      articleMode ? `${title}, 원문 열기` : `${title}, 댓글 ${comments}개, 원문 열기`
    );
  }

  const titleText = document.createElement("span");
  titleText.className = "post-title-text";
  titleText.textContent = title;
  const commentCount = document.createElement("span");
  commentCount.className = "post-comment-count";
  commentCount.setAttribute("aria-hidden", "true");
  commentCount.textContent = `[${numberFormatter.format(comments)}]`;
  content.append(titleText);
  if (!articleMode) {
    content.append(commentCount);
  }

  if (!safeUrl && !articleMode) {
    const commentDescription = document.createElement("span");
    commentDescription.className = "visually-hidden";
    commentDescription.textContent = `댓글 ${comments}개`;
    content.append(commentDescription);
  }

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
  const selectedLabel = state.topicId > 0
    ? String(state.archive?.selected_topic?.label || "").trim()
    : "";

  if (selectedLabel) {
    elements.resultCount.textContent =
      `‘${selectedLabel}’ 관련 글 ${filtered}개 · 전체 ${total}개`;
  } else if (view.filteredPosts === view.totalPosts) {
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

function restoreArchiveTabFocus() {
  if (!state.focusArchiveTabAfterLoad) {
    return;
  }

  state.focusArchiveTabAfterLoad = false;
  const selectedTab = elements.archiveTabs.querySelector('[role="tab"][aria-selected="true"]');
  selectedTab?.focus({ preventScroll: true });
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
      normalizeSignedInteger(right.upvotes, 0) - normalizeSignedInteger(left.upvotes, 0) ||
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
  elements.filterToggle.addEventListener("click", () => {
    const expanded = elements.filterToggle.getAttribute("aria-expanded") === "true";
    setMobileFiltersExpanded(!expanded);
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
  elements.topicPanelToggle.addEventListener("click", () => {
    if (isCompactTopicPanel()) {
      setTopicPanelExpanded(!state.topicPanelExpanded);
    }
  });
  elements.topicClear.addEventListener("click", () => applyTopicFilter(0));
  if (typeof window.matchMedia === "function") {
    const topicPanelMedia = window.matchMedia(COMPACT_TOPIC_PANEL_QUERY);
    topicPanelMedia.addEventListener?.("change", syncTopicPanelForViewport);
  }
  elements.rulesOpen.addEventListener("click", openRulesDialog);
  elements.rulesClose.addEventListener("click", requestCloseRulesDialog);
  elements.rulesDialog.addEventListener("click", (event) => {
    if (event.target === elements.rulesDialog) {
      requestCloseRulesDialog();
    }
  });
  elements.rulesDialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    requestCloseRulesDialog();
  });
  elements.rulesDialog.addEventListener("close", () => {
    elements.rulesOpen.focus({ preventScroll: true });
  });
  elements.preferenceForm.addEventListener("submit", (event) => {
    event.preventDefault();
    void savePreferenceDocument();
  });
  elements.preferenceDocument.addEventListener("input", syncPreferenceEditor);
  elements.preferenceClear.addEventListener("click", clearPreferenceDocumentDraft);
  elements.preferenceReset.addEventListener("click", resetPreferenceDocumentDraft);
  elements.hiddenOpen.addEventListener("click", openHiddenDialog);
  elements.hiddenClose.addEventListener("click", () => elements.hiddenDialog.close());
  elements.hiddenDialog.addEventListener("click", (event) => {
    if (event.target === elements.hiddenDialog) {
      elements.hiddenDialog.close();
    }
  });
  elements.feedbackDialogClose.addEventListener("click", () => {
    elements.feedbackDialog.close();
  });
  elements.feedbackDialog.addEventListener("click", (event) => {
    if (event.target === elements.feedbackDialog) {
      elements.feedbackDialog.close();
    }
  });
  elements.feedbackDialog.addEventListener("close", () => {
    const trigger = state.feedbackDialogTrigger;
    state.feedbackDialogPost = null;
    state.feedbackDialogTrigger = null;
    elements.feedbackDialogToolbar.replaceChildren();
    elements.feedbackDialogToolbar.removeAttribute("data-post-key");
    if (trigger?.isConnected) {
      trigger.focus({ preventScroll: true });
    }
  });
  elements.feedbackDialogToolbar.addEventListener("keydown", handleFeedbackToolbarKeydown);
  elements.feedbackUndo.addEventListener("click", () => {
    const undoAction = state.undoAction;
    if (typeof undoAction !== "function") {
      return;
    }
    state.undoAction = null;
    elements.feedbackUndo.disabled = true;
    Promise.resolve(undoAction())
      .catch((error) => showFeedbackToast(`실행 취소에 실패했습니다: ${error.message}`))
      .finally(() => {
        elements.feedbackUndo.disabled = false;
      });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      for (const dialog of [
        elements.runsDrawer,
        elements.feedbackDialog,
        elements.hiddenDialog,
      ]) {
        if (dialog.open) {
          event.preventDefault();
          dialog.close();
          break;
        }
      }
    }
  });
  window.addEventListener("popstate", () => {
    window.clearTimeout(state.filterTimer);
    hydrateStateFromUrl();
    state.focusPageContentAfterLoad = false;
    state.focusArchiveTabAfterLoad = false;
    writeStateToControls();
    setMobileFiltersExpanded(hasActiveFilterState());
    loadArchive();
  });
}

function hasActiveFilterState() {
  return Boolean(
    state.search ||
    state.subject ||
    state.minUpvotes !== DEFAULT_STATE.minUpvotes ||
    state.minComments !== DEFAULT_STATE.minComments ||
    state.sortBy !== DEFAULT_STATE.sortBy ||
    state.pageSize !== DEFAULT_STATE.pageSize
  );
}

function setMobileFiltersExpanded(expanded) {
  elements.filterShell.classList.toggle("is-filter-expanded", expanded);
  elements.filterToggle.setAttribute("aria-expanded", String(expanded));
  elements.filterToggleState.textContent = expanded ? "접기" : "펼치기";
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

function openRulesDialog() {
  if (typeof elements.rulesDialog.showModal !== "function") {
    return;
  }
  elements.rulesDialog.showModal();
  elements.rulesStatus.textContent = "선호 전문을 불러오는 중입니다.";
  state.preferenceDocument.loaded = false;
  syncPreferenceEditor();
  void refreshPreferenceDocument();
}

function requestCloseRulesDialog() {
  if (
    isPreferenceDocumentDirty()
    && !window.confirm("저장하지 않은 선호 전문 변경을 버리고 닫을까요?")
  ) {
    return;
  }
  elements.rulesDialog.close();
}

function isPreferenceDocumentDirty() {
  return state.preferenceDocument.loaded
    && elements.preferenceDocument.value !== state.preferenceDocument.content;
}

function syncPreferenceEditor() {
  const value = String(elements.preferenceDocument.value || "");
  const loaded = state.preferenceDocument.loaded;
  const dirty = isPreferenceDocumentDirty();
  const disabled = !loaded || state.preferenceSaving;
  elements.preferenceCount.textContent = numberFormatter.format(value.length);
  elements.preferenceDocument.disabled = disabled;
  elements.preferenceSave.disabled = disabled || !dirty;
  elements.preferenceReset.disabled = disabled || !dirty;
  elements.preferenceClear.disabled = disabled || !value;
}

function renderPreferenceDocument(document) {
  const content = typeof document?.content === "string" ? document.content : "";
  const version = Number(document?.version);
  const maxLength = Number(document?.max_length);
  state.preferenceDocument = {
    content,
    version: Number.isSafeInteger(version) && version >= 0 ? version : 0,
    updatedAt: document?.updated_at || null,
    maxLength: Number.isSafeInteger(maxLength) && maxLength > 0 ? maxLength : 1000,
    loaded: true,
  };
  elements.preferenceDocument.maxLength = state.preferenceDocument.maxLength;
  elements.preferenceDocument.value = content;
  const savedAt = getDateTime(state.preferenceDocument.updatedAt);
  elements.preferenceUpdated.textContent = state.preferenceDocument.version
    ? `마지막 저장 ${dateTimeFormatter.format(savedAt)} · 버전 ${numberFormatter.format(state.preferenceDocument.version)}`
    : "아직 저장된 선호 전문이 없습니다.";
  syncPreferenceEditor();
}

async function refreshPreferenceDocument() {
  try {
    const payload = await fetchGameNewsJson("/api/game-news/preferences");
    renderPreferenceDocument(payload?.document);
    elements.rulesStatus.textContent = state.preferenceDocument.content
      ? "표시된 전문 전체가 현재 적용 중입니다."
      : "현재 선호 전문이 비어 있습니다.";
    elements.preferenceDocument.focus();
  } catch (error) {
    elements.rulesStatus.textContent = `선호 전문을 불러오지 못했습니다: ${error.message}`;
    state.preferenceDocument.loaded = false;
    syncPreferenceEditor();
    elements.rulesClose.focus();
  }
}

function clearPreferenceDocumentDraft() {
  elements.preferenceDocument.value = "";
  syncPreferenceEditor();
  elements.rulesStatus.textContent = "저장을 누르면 선호 전문이 비워집니다.";
  elements.preferenceDocument.focus();
}

function resetPreferenceDocumentDraft() {
  elements.preferenceDocument.value = state.preferenceDocument.content;
  syncPreferenceEditor();
  elements.rulesStatus.textContent = "저장된 내용으로 되돌렸습니다.";
  elements.preferenceDocument.focus();
}

async function savePreferenceDocument() {
  if (!isPreferenceDocumentDirty() || state.preferenceSaving) {
    return;
  }
  state.preferenceSaving = true;
  syncPreferenceEditor();
  elements.rulesStatus.textContent = "선호 전문을 저장하는 중입니다.";
  try {
    const payload = await postGameNewsJson("/api/game-news/preferences", {
      content: elements.preferenceDocument.value,
      base_version: state.preferenceDocument.version,
      idempotency_key: createRequestKey("preference-document"),
    });
    renderPreferenceDocument(payload?.document);
    elements.rulesStatus.textContent = state.preferenceDocument.content
      ? "선호 전문을 저장했습니다. 다음 수집부터 적용합니다."
      : "선호 전문을 비웠습니다. 다음 수집부터 명시 규칙 없이 판단합니다.";
    elements.preferenceDocument.focus();
  } catch (error) {
    elements.rulesStatus.textContent = `선호 전문을 저장하지 못했습니다: ${error.message}`;
  } finally {
    state.preferenceSaving = false;
    syncPreferenceEditor();
  }
}

function openHiddenDialog() {
  if (typeof elements.hiddenDialog.showModal !== "function") {
    return;
  }
  elements.hiddenDialog.showModal();
  elements.hiddenStatus.textContent = "숨긴 글을 불러오는 중입니다.";
  void refreshHiddenItems();
}

async function refreshHiddenItems() {
  try {
    const payload = await fetchGameNewsJson("/api/game-news/hidden");
    const items = Array.isArray(payload?.items) ? payload.items : [];
    elements.hiddenCount.textContent = numberFormatter.format(items.length);
    renderHiddenItems(items);
    elements.hiddenStatus.textContent = items.length
      ? `숨긴 글 ${numberFormatter.format(items.length)}개`
      : "숨긴 글이 없습니다.";
  } catch (error) {
    elements.hiddenStatus.textContent = `숨긴 글을 불러오지 못했습니다: ${error.message}`;
    elements.hiddenList.replaceChildren();
  }
}

async function refreshHiddenCount() {
  try {
    const payload = await fetchGameNewsJson("/api/game-news/hidden");
    elements.hiddenCount.textContent = numberFormatter.format(
      Array.isArray(payload?.items) ? payload.items.length : 0
    );
  } catch {
    elements.hiddenCount.textContent = "-";
  }
}

function renderHiddenItems(items) {
  elements.hiddenList.replaceChildren();
  for (const post of items) {
    const postKey = normalizeFeedbackKey(post?.post_key);
    if (!postKey) {
      continue;
    }
    const item = document.createElement("div");
    item.className = "management-item";
    const copy = document.createElement("div");
    copy.className = "management-item-copy";
    const title = document.createElement("p");
    const safeUrl = getSafeHttpUrl(post.post_url);
    if (safeUrl) {
      const link = document.createElement("a");
      link.href = safeUrl;
      link.target = "_blank";
      link.rel = "noreferrer noopener";
      link.textContent = String(post.title || "제목 없음");
      title.append(link);
    } else {
      title.textContent = String(post.title || "제목 없음");
    }
    const meta = document.createElement("p");
    meta.className = "management-item-meta";
    meta.textContent = String(post.subject || "주제 없음");
    copy.append(title, meta);
    const restore = document.createElement("button");
    restore.className = "button button-secondary";
    restore.type = "button";
    restore.textContent = "복구";
    restore.addEventListener("click", async () => {
      restore.disabled = true;
      try {
        await restoreHiddenPost(postKey);
        await refreshHiddenItems();
      } catch (error) {
        elements.hiddenStatus.textContent = `복구하지 못했습니다: ${error.message}`;
        restore.disabled = false;
      }
    });
    item.append(copy, restore);
    elements.hiddenList.append(item);
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
  normalizeContentState();
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
  const target = normalizeTarget(params.get("target"));

  state.target = target || DEFAULT_TARGET;
  state.search = String(params.get("q") || "").trim().slice(0, 100);
  state.subject = normalizeSubject(params.get("subject"));
  state.topicId = normalizePositiveNumber(params.get("topic"), 0);
  state.minUpvotes =
    minUpvotes === null
      ? DEFAULT_STATE.minUpvotes
      : normalizeNonNegativeNumber(minUpvotes, DEFAULT_STATE.minUpvotes);
  state.minComments = normalizeNonNegativeNumber(params.get("min_comments"), 0);
  state.sortBy = VALID_SORTS.has(sortBy) ? sortBy : DEFAULT_STATE.sortBy;
  state.page = normalizePositiveNumber(params.get("page"), 1);
  state.pageSize = VALID_PAGE_SIZES.has(pageSize) ? pageSize : DEFAULT_STATE.pageSize;
  normalizeContentState();
}

function syncStateToUrl({ replace = true } = {}) {
  const url = new URL(window.location.href);
  const values = {
    target: state.target,
    q: state.search || null,
    subject: state.subject || null,
    topic: state.topicId || null,
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

  const method = replace ? "replaceState" : "pushState";
  window.history[method](null, "", url);
}

function renderLoadingState() {
  applyContentKindMode();
  renderArchiveTabs();
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

function normalizeTarget(value) {
  const target = String(value || "").trim();
  return TARGET_PATTERN.test(target) ? target : "";
}

function normalizeNonNegativeNumber(value, fallback) {
  const number = Number.parseInt(value, 10);
  return Number.isNaN(number) || number < 0 ? fallback : number;
}

function normalizeSignedInteger(value, fallback) {
  const number = Number(value);
  return Number.isSafeInteger(number) ? number : fallback;
}

function normalizePositiveNumber(value, fallback) {
  const number = Number.parseInt(value, 10);
  return Number.isNaN(number) || number < 1 ? fallback : number;
}

initialize();
