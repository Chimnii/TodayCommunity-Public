import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join, normalize, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const dashboardRoot = resolve(fileURLToPath(new URL("../dashboard/", import.meta.url)));
const port = Number.parseInt(process.env.TC_FIXTURE_PORT || "4173", 10);

const posts = Array.from({ length: 73 }, (_, index) => {
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
  const search = String(requestUrl.searchParams.get("q") || "").trim().toLocaleLowerCase("ko-KR");
  const subject = normalizeSubject(requestUrl.searchParams.get("subject"));
  const minUpvotes = normalizeNonNegative(requestUrl.searchParams.get("min_upvotes"));
  const minComments = normalizeNonNegative(requestUrl.searchParams.get("min_comments"));
  const sortBy = ["created_at", "upvotes", "comments"].includes(requestUrl.searchParams.get("sort"))
    ? requestUrl.searchParams.get("sort")
    : "created_at";
  const pageSize = Math.min(normalizePositive(requestUrl.searchParams.get("page_size"), 30), 100);
  const requestedPage = normalizePositive(requestUrl.searchParams.get("page"), 1);

  const filtered = posts
    .filter((post) => {
      return (
        post.upvotes >= minUpvotes &&
        post.comments >= minComments &&
        (!subject || post.subject === subject) &&
        (!search || post.title.toLocaleLowerCase("ko-KR").includes(search))
      );
    })
    .sort((left, right) => comparePosts(left, right, sortBy));

  const totalPages = filtered.length === 0 ? 0 : Math.ceil(filtered.length / pageSize);
  const page = totalPages === 0 ? 1 : Math.min(requestedPage, totalPages);
  const offset = (page - 1) * pageSize;
  const visiblePosts = filtered.slice(offset, offset + pageSize);

  sendJson(response, {
    target: "dcinside-singularity",
    source: {
      source_key: "dcinside-singularity",
      site_name: "dcinside",
      board_name: "특이점이 온다 마이너 갤러리",
      board_url: "https://gall.dcinside.com/mgallery/board/lists/?id=thesingularity",
      min_upvotes: 4,
      min_comments: 20,
      updated_at: "2026-07-17T00:30:00.000Z",
    },
    summary: {
      total_posts: posts.length,
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
    subject_options: subjectOptions,
    runs,
    posts: visiblePosts,
  });
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
  const requestedPath = requestUrl.pathname === "/" ? "/index.html" : requestUrl.pathname;
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
