import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const dashboardRoot = resolve(fileURLToPath(new URL("../dashboard/", import.meta.url)));
const port = Number.parseInt(process.env.TC_FIXTURE_PORT || "4173", 10);
const requestedPostCount = Number.parseInt(process.env.TC_FIXTURE_POST_COUNT || "73", 10);
const fixtureAuthState = process.env.TC_FIXTURE_AUTH_STATE === "guest"
  ? "guest"
  : "authenticated";
const postCount = Number.isInteger(requestedPostCount) && requestedPostCount > 0
  ? Math.min(requestedPostCount, 1000)
  : 73;
const feedbackByPost = new Map();
const hiddenPostKeys = new Set();
let preferenceDocument = {
  content: "LCK를 포함한 e스포츠 관련 기사는 역대급 이벤트나 중대한 이슈가 아니라면 수집하지 않는다.",
  version: 1,
  updated_at: "2026-08-17T16:09:27.289Z",
  max_length: 1000,
};
const ARTICLE_SUBJECTS = Object.freeze([
  "business",
  "development",
  "store",
  "policy",
  "technology",
  "esports",
  "release",
  "other",
]);
const COMMUNITY_TOPIC_FIXTURES = Object.freeze({
  "dcinside-singularity": Object.freeze([
    { topic_id: 101, label: "GPT-5.6 공개", post_count: 12, previous_post_count: 4, trend_state: "rising" },
    { topic_id: 102, label: "휴머노이드 로봇", post_count: 8, previous_post_count: 0, trend_state: "new" },
    { topic_id: 103, label: "AI 에이전트", post_count: 6, previous_post_count: 5, trend_state: "active" },
  ]),
  "dcinside-agent-stack": Object.freeze([
    { topic_id: 201, label: "로컬 에이전트", post_count: 9, previous_post_count: 3, trend_state: "rising" },
    { topic_id: 202, label: "브라우저 자동화", post_count: 5, previous_post_count: 0, trend_state: "new" },
  ]),
  "fmkorea-munich": Object.freeze([
    { topic_id: 301, label: "분데스리가 개막", post_count: 7, previous_post_count: 2, trend_state: "rising" },
    { topic_id: 302, label: "이적시장", post_count: 5, previous_post_count: 5, trend_state: "active" },
  ]),
});

const archives = [
  {
    archive_key: "dcinside-singularity",
    display_name: "특이점이 온다",
    description: "디시인사이드 특이점이 온다 갤러리 인기글",
    content_kind: "community",
    display_order: 10,
    updated_at: "2026-07-17T00:30:00.000Z",
  },
  {
    archive_key: "dcinside-agent-stack",
    display_name: "에이전트 스택",
    description: "디시인사이드 에이전트 스택 갤러리 인기글",
    content_kind: "community",
    display_order: 20,
    updated_at: "2026-07-17T00:30:00.000Z",
  },
  {
    archive_key: "fmkorea-munich",
    display_name: "뮌헨",
    description: "에펨코리아의 뮌헨 관련 인기글",
    content_kind: "community",
    display_order: 30,
    updated_at: "2026-07-17T00:30:00.000Z",
  },
  {
    archive_key: "game-news",
    display_name: "게임 뉴스",
    description: "인벤과 디스이즈게임에서 선별한 게임 뉴스",
    content_kind: "article",
    display_order: 40,
    updated_at: "2026-07-17T00:30:00.000Z",
  },
  {
    archive_key: "all",
    display_name: "모두",
    description: "모든 공개 아카이브의 글",
    content_kind: "mixed",
    display_order: 100,
    updated_at: "2026-07-17T00:30:00.000Z",
  },
];

const sourcesByArchive = {
  "dcinside-singularity": [
    {
      source_key: "dcinside-singularity",
      archive_key: "dcinside-singularity",
      site_name: "dcinside",
      board_name: "특이점이 온다 마이너 갤러리",
      board_url: "https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity",
      min_upvotes: 4,
      min_comments: 20,
    },
  ],
  "dcinside-agent-stack": [
    {
      source_key: "dcinside-agent-stack",
      archive_key: "dcinside-agent-stack",
      site_name: "dcinside",
      board_name: "에이전트 스택(Agent Stack) 마이너 갤러리",
      board_url: "https://gall.dcinside.com/mgallery/board/lists/?id=agent_stack",
      min_upvotes: 10,
      min_comments: 100,
    },
  ],
  "fmkorea-munich": [
    {
      source_key: "fmkorea-best-munich-search",
      archive_key: "fmkorea-munich",
      site_name: "fmkorea",
      board_name: "포텐 터짐 '뮌헨' 검색",
      board_url: "https://www.fmkorea.com/search.php?mid=best&search_keyword=%EB%AE%8C%ED%97%A8&search_target=title_content",
      min_upvotes: 0,
      min_comments: 0,
    },
    {
      source_key: "fmkorea-best-bayern-search",
      archive_key: "fmkorea-munich",
      site_name: "fmkorea",
      board_name: "포텐 터짐 '바이에른' 검색",
      board_url: "https://www.fmkorea.com/search.php?mid=best&search_keyword=%EB%B0%94%EC%9D%B4%EC%97%90%EB%A5%B8&search_target=title_content",
      min_upvotes: 0,
      min_comments: 0,
    },
    {
      source_key: "fmkorea-bayern-board",
      archive_key: "fmkorea-munich",
      site_name: "fmkorea",
      board_name: "해외축구 바이에른 게시판",
      board_url: "https://www.fmkorea.com/index.php?mid=football_world&category=853073246",
      min_upvotes: 13,
      min_comments: 130,
    },
  ],
  "game-news": [
    {
      source_key: "game-news-inven",
      archive_key: "game-news",
      site_name: "inven",
      board_name: "인벤 게임 뉴스",
      board_url: "https://www.inven.co.kr/webzine/news/",
      min_upvotes: 0,
      min_comments: 0,
    },
    {
      source_key: "game-news-thisisgame",
      archive_key: "game-news",
      site_name: "thisisgame",
      board_name: "디스이즈게임",
      board_url: "https://www.thisisgame.com/",
      min_upvotes: 0,
      min_comments: 0,
    },
  ],
};
sourcesByArchive.all = Object.entries(sourcesByArchive)
  .filter(([archiveKey]) => archiveKey !== "all")
  .flatMap(([, archiveSources]) => archiveSources);

const posts = Array.from({ length: postCount }, (_, index) => {
  const id = 1324407 - index;
  const upvotes = (index * 7) % 31;
  const comments = (index * 11) % 46;
  const createdAt = new Date(Date.UTC(2026, 6, 17, 0, 30) - index * 45 * 60 * 1000).toISOString();
  const subject =
    index === 0
      ? ""
      : index === 1
        ? "☕작업잡담"
        : index === 2
          ? "👨‍👩‍👧‍👦AI잡담"
          : index === 3
            ? "양자 컴퓨팅"
            : index % 5 === 0
              ? "양자 컴퓨팅"
              : index % 3 === 0
                ? "로봇 연구"
                : "인공지능 소식";
  const qualifies = 5 * upvotes + comments >= 20;
  const qualifiesBy = !qualifies
    ? "none"
    : upvotes >= 4 && comments >= 20
      ? "upvotes+comments"
      : comments >= 20
        ? "comments"
        : upvotes >= 4
          ? "upvotes"
          : "upvotes+comments";

  return {
    source_key: "dcinside-singularity",
    external_post_id: String(id),
    subject,
    title: `아카이브 검증 게시글 ${index + 1}`,
    post_url: `https://gall.dcinside.com/mgallery/board/view/?id=thesingularity&no=${id}`,
    created_at: createdAt,
    created_at_raw: createdAt,
    upvotes,
    comments,
    qualifies_by: qualifiesBy,
    fetched_at: createdAt,
    first_seen_at: createdAt,
    last_seen_at: createdAt,
    status: "active",
  };
});
const subjectOptions = [...new Set(posts.map((post) => normalizeSubject(post.subject)).filter(Boolean))]
  .sort((left, right) => left.localeCompare(right, "ko-KR"))
  .slice(0, 100);

const runs = Array.from({ length: 10 }, (_, index) => ({
  source_key: "dcinside-singularity",
  run_type: index % 3 === 0 ? "hot" : index % 3 === 1 ? "finalizer" : "backfill",
  status: index === 2 ? "failed" : index === 5 ? "blocked" : "success",
  scanned_pages: index + 1,
  scanned_posts: 47 + index * 3,
  matched_posts: 2 + (index % 4),
  started_at: new Date(Date.UTC(2026, 6, 17, 0, 7) - index * 30 * 60 * 1000).toISOString(),
  finished_at: new Date(Date.UTC(2026, 6, 17, 0, 18) - index * 30 * 60 * 1000).toISOString(),
  error_message: index === 2 ? "Fixture 오류: 원격 목록 응답을 확인하지 못했습니다." : null,
}));

const mimeTypes = {
  ".css": "text/css; charset=UTF-8",
  ".html": "text/html; charset=UTF-8",
  ".js": "text/javascript; charset=UTF-8",
  ".json": "application/json; charset=UTF-8",
};

function sendJson(response, body, statusCode = 200) {
  response.writeHead(statusCode, {
    "cache-control": "no-store",
    "content-type": "application/json; charset=UTF-8",
  });
  response.end(JSON.stringify(body));
}

function handleArchive(requestUrl, response) {
  const target = String(requestUrl.searchParams.get("target") || "dcinside-singularity");
  const archive = archives.find((candidate) => candidate.archive_key === target);
  if (!archive) {
    sendJson(response, { error: "Unknown archive target." }, 400);
    return;
  }
  const sources = sourcesByArchive[target];
  const articleMode = archive.content_kind === "article";
  const mixedMode = archive.content_kind === "mixed";
  const topicFixtures = COMMUNITY_TOPIC_FIXTURES[target] || [];
  const contentArchives = archives.filter((candidate) => candidate.archive_key !== "all");
  const archivePosts = posts.map((post, index) => {
    const postArchive = mixedMode
      ? contentArchives[index % contentArchives.length]
      : archive;
    const postSources = sourcesByArchive[postArchive.archive_key];
    const postSource = postSources[index % postSources.length];
    const postIsArticle = postArchive.content_kind === "article";
    return {
      ...post,
      archive_key: postArchive.archive_key,
      source_key: postSource.source_key,
      subject: postIsArticle
        ? ARTICLE_SUBJECTS[index % ARTICLE_SUBJECTS.length]
        : post.subject,
      title: postIsArticle ? `게임 뉴스 검증 기사 ${index + 1}` : post.title,
      post_url: postIsArticle
        ? index % 2 === 0
          ? `https://www.inven.co.kr/webzine/news/?news=${post.external_post_id}`
          : `https://www.thisisgame.com/webzine/news/nboard/4/?n=${post.external_post_id}`
        : post.post_url,
      upvotes: postIsArticle ? 0 : post.upvotes,
      comments: postIsArticle ? 0 : post.comments,
      qualifies_by: postIsArticle ? "llm-include" : post.qualifies_by,
      feedback_key: postIsArticle ? feedbackKeyForIndex(index) : undefined,
      topic_ids: postIsArticle || mixedMode || topicFixtures.length === 0
        ? []
        : [topicFixtures[index % topicFixtures.length].topic_id],
    };
  }).filter((post) => post.archive_key !== "game-news" || !hiddenPostKeys.has(post.feedback_key));
  const archiveSubjectOptions = [
    ...new Set(archivePosts.map((post) => normalizeSubject(post.subject)).filter(Boolean)),
  ]
    .sort((left, right) => left.localeCompare(right, "ko-KR"))
    .slice(0, 100);
  const search = String(requestUrl.searchParams.get("q") || "").trim().toLocaleLowerCase("ko-KR");
  const subject = normalizeSubject(requestUrl.searchParams.get("subject"));
  const minUpvotes = normalizeNonNegative(requestUrl.searchParams.get("min_upvotes"));
  const minComments = normalizeNonNegative(requestUrl.searchParams.get("min_comments"));
  const sortBy = ["created_at", "upvotes", "comments"].includes(requestUrl.searchParams.get("sort"))
    ? requestUrl.searchParams.get("sort")
    : "created_at";
  const pageSize = Math.min(normalizePositive(requestUrl.searchParams.get("page_size"), 30), 100);
  const requestedPage = normalizePositive(requestUrl.searchParams.get("page"), 1);
  const topicId = normalizePositive(requestUrl.searchParams.get("topic"), 0);

  const filtered = archivePosts
    .filter((post) => {
      return (
        post.upvotes >= minUpvotes &&
        post.comments >= minComments &&
        (!subject || post.subject === subject) &&
        (!topicId || post.topic_ids.includes(topicId)) &&
        (!search || post.title.toLocaleLowerCase("ko-KR").includes(search))
      );
    })
    .sort((left, right) => comparePosts(left, right, sortBy));

  const totalPages = filtered.length === 0 ? 0 : Math.ceil(filtered.length / pageSize);
  const page = totalPages === 0 ? 1 : Math.min(requestedPage, totalPages);
  const offset = (page - 1) * pageSize;
  const visiblePosts = filtered.slice(offset, offset + pageSize);
  const selectedTopic = topicFixtures.find((topic) => topic.topic_id === topicId) || null;
  const topicTrends = articleMode || mixedMode
    ? null
    : {
        window_hours: 24,
        window_start: "2026-07-16T00:30:00.000Z",
        window_end: "2026-07-17T00:30:00.000Z",
        generated_at: "2026-07-17T00:31:00.000Z",
        summary: topicFixtures.length
          ? `최근 24시간에는 ‘${topicFixtures[0].label}’ 관련 글이 많이 다뤄졌습니다.`
          : "최근 24시간에는 반복해서 다뤄진 주요 토픽이 아직 없습니다.",
        eligible_post_count: archivePosts.length,
        analyzed_post_count: archivePosts.length,
        topics: topicFixtures.map((topic) => {
          const representative = archivePosts.find((post) => post.topic_ids.includes(topic.topic_id));
          return {
            ...topic,
            hotness_score: topic.post_count * 10,
            representative_posts: representative
              ? [{
                  external_post_id: representative.external_post_id,
                  title: representative.title,
                  post_url: representative.post_url,
                  created_at: representative.created_at,
                }]
              : [],
          };
        }),
      };

  sendJson(response, {
    target,
    archives,
    archive,
    sources,
    source: sources[0],
    selected_topic: selectedTopic,
    topic_trends: topicTrends,
    summary: {
      total_posts: archivePosts.length,
      filtered_posts: filtered.length,
      latest_seen_at: posts[0].last_seen_at,
      exported_posts: visiblePosts.length,
      recent_runs: runs.length,
    },
    pagination: {
      page,
      page_size: pageSize,
      total_pages: totalPages,
      visible_from: visiblePosts.length ? offset + 1 : 0,
      visible_to: visiblePosts.length ? offset + visiblePosts.length : 0,
      has_previous: page > 1,
      has_next: totalPages > 0 && page < totalPages,
    },
    subject_options: articleMode || mixedMode ? archiveSubjectOptions : subjectOptions,
    runs: runs.map((run, index) => ({
      ...run,
      source_key: sources[index % sources.length].source_key,
      board_name: sources[index % sources.length].board_name,
    })),
    posts: visiblePosts,
  });
}

function sendFixtureSession(response) {
  const authenticated = fixtureAuthState === "authenticated";
  sendJson(response, {
    actor: authenticated ? "owner:primary-v1" : null,
    authentication: fixtureAuthState,
    state: fixtureAuthState,
    authenticated,
    capabilities: {
      rate: authenticated,
      hide: authenticated,
      manage_rules: authenticated,
      manage_auth: false,
    },
  });
}

function feedbackKeyForIndex(index) {
  return (index + 1).toString(16).padStart(32, "0");
}

async function readJson(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
}

function fixtureArticle(postKey) {
  const index = Number.parseInt(postKey, 16) - 1;
  const post = posts[index];
  if (!post || feedbackKeyForIndex(index) !== postKey) {
    return null;
  }
  return {
    post_key: postKey,
    title: `게임 뉴스 검증 기사 ${index + 1}`,
    post_url: index % 2 === 0
      ? `https://www.inven.co.kr/webzine/news/?news=${post.external_post_id}`
      : `https://www.thisisgame.com/webzine/news/nboard/4/?n=${post.external_post_id}`,
    subject: ARTICLE_SUBJECTS[index % ARTICLE_SUBJECTS.length],
    last_seen_at: post.last_seen_at,
  };
}

async function handleGameNewsApi(request, requestUrl, response) {
  const resource = requestUrl.pathname.split("/").at(-1);
  if (request.method === "GET" && resource === "session") {
    sendFixtureSession(response);
    return;
  }
  if (request.method === "GET" && resource === "feedback") {
    const items = requestUrl.searchParams.getAll("post_key").map((postKey) => ({
      post_key: postKey,
      rating_level: feedbackByPost.get(postKey)?.rating_level ?? null,
      feedback_version: feedbackByPost.get(postKey)?.version ?? 0,
      reason_code: null,
      hidden: hiddenPostKeys.has(postKey),
    }));
    sendJson(response, { items });
    return;
  }
  if (request.method === "GET" && resource === "hidden") {
    sendJson(response, {
      items: [...hiddenPostKeys].map(fixtureArticle).filter(Boolean),
    });
    return;
  }
  if (request.method === "GET" && resource === "preferences") {
    sendJson(response, { document: preferenceDocument });
    return;
  }
  if (request.method !== "POST") {
    sendJson(response, { error: "Unsupported fixture route" }, 404);
    return;
  }
  const body = await readJson(request);
  if (resource === "feedback") {
    const current = feedbackByPost.get(body.post_key);
    const item = {
      post_key: body.post_key,
      rating_level: body.rating_level,
      feedback_version: (current?.version ?? 0) + 1,
      reason_code: body.reason_code ?? null,
      hidden: hiddenPostKeys.has(body.post_key),
    };
    feedbackByPost.set(body.post_key, { ...item, version: item.feedback_version });
    sendJson(response, { item }, 201);
    return;
  }
  if (resource === "visibility") {
    if (body.action === "hide") hiddenPostKeys.add(body.post_key);
    if (body.action === "restore") hiddenPostKeys.delete(body.post_key);
    sendJson(response, { item: { post_key: body.post_key, hidden: body.action === "hide" } }, 201);
    return;
  }
  if (resource === "preferences") {
    if (body.base_version !== preferenceDocument.version) {
      sendJson(response, {
        error: "다른 기기에서 선호 전문이 변경되었습니다. 최신 내용을 다시 불러와 주세요.",
      }, 409);
      return;
    }
    preferenceDocument = {
      content: String(body.content || "").trim(),
      version: preferenceDocument.version + 1,
      updated_at: new Date().toISOString(),
      max_length: 1000,
    };
    sendJson(response, { document: preferenceDocument }, 201);
    return;
  }
  sendJson(response, { error: "Unsupported fixture route" }, 404);
}

function comparePosts(left, right, sortBy) {
  if (sortBy === "upvotes") {
    return right.upvotes - left.upvotes || compareCreatedAt(left, right) || compareId(left, right);
  }
  if (sortBy === "comments") {
    return right.comments - left.comments || compareCreatedAt(left, right) || compareId(left, right);
  }
  return compareCreatedAt(left, right) || compareId(left, right);
}

function compareCreatedAt(left, right) {
  return new Date(right.created_at).getTime() - new Date(left.created_at).getTime();
}

function compareId(left, right) {
  return Number(right.external_post_id) - Number(left.external_post_id);
}

function normalizeNonNegative(value) {
  const number = Number.parseInt(value || "0", 10);
  return Number.isNaN(number) || number < 0 ? 0 : number;
}

function normalizePositive(value, fallback) {
  const number = Number.parseInt(value || "", 10);
  return Number.isNaN(number) || number < 1 ? fallback : number;
}

function normalizeSubject(value) {
  const characters = Array.from(String(value || "").trim());
  return characters.length <= 100 ? characters.join("") : "";
}

async function serveStatic(requestUrl, response) {
  const requestedPath = requestUrl.pathname.endsWith("/")
    ? `${requestUrl.pathname}index.html`
    : requestUrl.pathname;
  const safeRelativePath = normalize(requestedPath).replace(/^[/\\]+/, "");
  const filePath = resolve(join(dashboardRoot, safeRelativePath));

  if (!filePath.startsWith(dashboardRoot)) {
    response.writeHead(403);
    response.end("Forbidden");
    return;
  }

  try {
    const fileStats = await stat(filePath);
    if (!fileStats.isFile()) {
      throw new Error("Not a file");
    }

    response.writeHead(200, {
      "cache-control": "no-store",
      "content-type": mimeTypes[extname(filePath)] || "application/octet-stream",
    });
    createReadStream(filePath).pipe(response);
  } catch {
    response.writeHead(404, { "content-type": "text/plain; charset=UTF-8" });
    response.end("Not found");
  }
}

const server = createServer(async (request, response) => {
  const requestUrl = new URL(request.url || "/", `http://${request.headers.host}`);
  if (requestUrl.pathname === "/api/archive") {
    handleArchive(requestUrl, response);
    return;
  }
  if (requestUrl.pathname.startsWith("/api/game-news/")) {
    await handleGameNewsApi(request, requestUrl, response);
    return;
  }
  if (requestUrl.pathname === "/api/auth/session" && request.method === "GET") {
    sendFixtureSession(response);
    return;
  }
  if (requestUrl.pathname === "/api/auth/secret/exchange" && request.method === "POST") {
    sendJson(response, { state: "authenticated" });
    return;
  }
  await serveStatic(requestUrl, response);
});

server.listen(port, "127.0.0.1", () => {
  console.log(`READY http://127.0.0.1:${port}`);
});

function shutdown() {
  server.close(() => process.exit(0));
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
