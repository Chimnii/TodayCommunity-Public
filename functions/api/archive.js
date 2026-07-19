const DEFAULT_TARGET = "dcinside-singularity";
const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 30;
const MAX_PAGE_SIZE = 100;
const MAX_SEARCH_LENGTH = 100;
const MAX_SUBJECT_LENGTH = 100;
const MAX_SUBJECT_OPTIONS = 100;
const ALLOWED_TARGETS = new Set([DEFAULT_TARGET]);

const SORT_COLUMNS = {
  created_at: "created_at",
  upvotes: "upvotes",
  comments: "comments",
};

function jsonResponse(body, status = 200) {
  const cacheControl =
    status >= 400
      ? "no-store"
      : "public, max-age=15, s-maxage=60, stale-while-revalidate=120";
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=UTF-8",
      "cache-control": cacheControl,
      "x-content-type-options": "nosniff",
    },
  });
}

function normalizePositiveInteger(rawValue, fallback, max = Number.MAX_SAFE_INTEGER) {
  const value = (rawValue ?? "").trim();
  if (!/^\d+$/.test(value)) {
    return fallback;
  }

  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < 1) {
    return fallback;
  }

  return Math.min(parsed, max);
}

function normalizeMinimum(rawValue) {
  const value = (rawValue ?? "").trim();
  if (!/^\d+$/.test(value)) {
    return 0;
  }

  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : 0;
}

function normalizeSearch(rawValue) {
  return Array.from((rawValue ?? "").trim()).slice(0, MAX_SEARCH_LENGTH).join("");
}

function normalizeSubject(rawValue) {
  const characters = Array.from((rawValue ?? "").trim());
  return characters.length <= MAX_SUBJECT_LENGTH ? characters.join("") : null;
}

function escapeLike(value) {
  return value.replace(/[\\%_]/g, "\\$&");
}

function normalizeCount(value) {
  const count = Number(value ?? 0);
  return Number.isFinite(count) && count > 0 ? Math.trunc(count) : 0;
}

function normalizeSubjectOptions(rawValue) {
  let values;

  try {
    values = Array.isArray(rawValue) ? rawValue : JSON.parse(rawValue || "[]");
  } catch {
    return [];
  }

  if (!Array.isArray(values)) {
    return [];
  }

  const subjects = new Set();
  for (const rawSubject of values) {
    if (typeof rawSubject !== "string") {
      continue;
    }

    const subject = rawSubject.trim();
    if (!subject || Array.from(subject).length > MAX_SUBJECT_LENGTH) {
      continue;
    }
    subjects.add(subject);
  }

  return [...subjects]
    .sort((left, right) => left.localeCompare(right, "ko-KR"))
    .slice(0, MAX_SUBJECT_OPTIONS);
}

function buildPostFilter(target, query, minUpvotes, minComments, subject) {
  const clauses = ["source_key = ?", "upvotes >= ?", "comments >= ?"];
  const bindings = [target, minUpvotes, minComments];

  if (subject) {
    clauses.push("subject = ?");
    bindings.push(subject);
  }

  if (query) {
    clauses.push("title LIKE ? ESCAPE '\\'");
    bindings.push(`%${escapeLike(query)}%`);
  }

  return {
    sql: clauses.join("\n          AND "),
    bindings,
  };
}

function buildOrderClause(sort) {
  const primaryColumn = SORT_COLUMNS[sort] ?? SORT_COLUMNS.created_at;
  if (primaryColumn === "created_at") {
    return "created_at DESC, id DESC";
  }
  return `${primaryColumn} DESC, created_at DESC, id DESC`;
}

function buildCacheKey(
  url,
  { target, pageSize, requestedPage, query, minUpvotes, minComments, subject, sort }
) {
  const cacheUrl = new URL(url.pathname, url.origin);
  const normalized = {
    target,
    page: String(requestedPage),
    page_size: String(pageSize),
    q: query,
    min_upvotes: String(minUpvotes),
    min_comments: String(minComments),
    subject,
    sort,
  };

  for (const [name, value] of Object.entries(normalized)) {
    if (value) {
      cacheUrl.searchParams.set(name, value);
    }
  }
  return new Request(cacheUrl.toString(), { method: "GET" });
}

export async function onRequestGet(context) {
  try {
    const url = new URL(context.request.url);
    const target = (url.searchParams.get("target") || DEFAULT_TARGET).trim();
    if (!ALLOWED_TARGETS.has(target)) {
      return jsonResponse({ error: "Unknown archive target." }, 400);
    }
    const pageSize = normalizePositiveInteger(
      url.searchParams.get("page_size"),
      DEFAULT_PAGE_SIZE,
      MAX_PAGE_SIZE
    );
    const maxPage = Math.max(1, Math.floor(Number.MAX_SAFE_INTEGER / pageSize));
    const requestedPage = normalizePositiveInteger(
      url.searchParams.get("page"),
      DEFAULT_PAGE,
      maxPage
    );
    const query = normalizeSearch(url.searchParams.get("q"));
    const minUpvotes = normalizeMinimum(url.searchParams.get("min_upvotes"));
    const minComments = normalizeMinimum(url.searchParams.get("min_comments"));
    const subject = normalizeSubject(url.searchParams.get("subject"));
    if (subject === null) {
      return jsonResponse({ error: "Subject filter must be 100 characters or fewer." }, 400);
    }
    const requestedSort = url.searchParams.get("sort") || "created_at";
    const sort = Object.hasOwn(SORT_COLUMNS, requestedSort) ? requestedSort : "created_at";
    const filter = buildPostFilter(target, query, minUpvotes, minComments, subject);
    const cacheKey = buildCacheKey(url, {
      target,
      pageSize,
      requestedPage,
      query,
      minUpvotes,
      minComments,
      subject,
      sort,
    });
    const edgeCache = globalThis.caches?.default;
    if (edgeCache) {
      const cached = await edgeCache.match(cacheKey);
      if (cached) {
        return cached;
      }
    }

    const db = context.env.DB;

    const source = await db
      .prepare(
        `
        SELECT source_key, site_name, board_name, board_url, min_upvotes, min_comments, updated_at
        FROM sources
        WHERE source_key = ?
        LIMIT 1
        `
      )
      .bind(target)
      .first();

    const summary = await db
      .prepare(
        `
        SELECT
          COUNT(*) AS total_posts,
          COALESCE(MAX(last_seen_at), '') AS latest_seen_at,
          (
            SELECT COALESCE(json_group_array(subject), '[]')
            FROM (
              SELECT DISTINCT TRIM(subject) AS subject
              FROM posts
              WHERE source_key = ?
                AND TRIM(subject) <> ''
                AND length(TRIM(subject)) <= ${MAX_SUBJECT_LENGTH}
              ORDER BY subject COLLATE NOCASE, subject
              LIMIT ${MAX_SUBJECT_OPTIONS}
            )
          ) AS subject_options_json
        FROM posts
        WHERE source_key = ?
        `
      )
      .bind(target, target)
      .first();

    const filteredSummary = await db
      .prepare(
        `
        SELECT COUNT(*) AS filtered_posts
        FROM posts
        WHERE ${filter.sql}
        `
      )
      .bind(...filter.bindings)
      .first();

    const filteredPosts = normalizeCount(filteredSummary?.filtered_posts);
    const totalPages = Math.ceil(filteredPosts / pageSize);
    const page = Math.min(requestedPage, Math.max(totalPages, 1));
    const offset = (page - 1) * pageSize;

    const runResult = await db
      .prepare(
        `
        SELECT source_key, run_type, status, scanned_pages, scanned_posts, matched_posts, started_at, finished_at,
               CASE
                 WHEN error_message IS NULL OR TRIM(error_message) = '' THEN 0
                 ELSE 1
               END AS had_error
        FROM crawl_runs
        WHERE source_key = ?
        ORDER BY id DESC
        LIMIT 10
        `
      )
      .bind(target)
      .all();

    const postResult = await db
      .prepare(
        `
        SELECT source_key, external_post_id, subject, title, post_url, created_at, created_at_raw, upvotes, comments,
               qualifies_by, fetched_at, first_seen_at, last_seen_at, status
        FROM posts
        WHERE ${filter.sql}
        ORDER BY ${buildOrderClause(sort)}
        LIMIT ? OFFSET ?
        `
      )
      .bind(...filter.bindings, pageSize, offset)
      .all();

    const runs = (runResult.results ?? []).map(
      ({ had_error: hadError, error_message: _discardedError, ...run }) => ({
        ...run,
        error_message: Number(hadError)
          ? "수집 처리 중 오류가 발생했습니다."
          : null,
      })
    );
    const posts = postResult.results ?? [];
    const totalPosts = normalizeCount(summary?.total_posts);
    const visibleFrom = posts.length > 0 ? offset + 1 : 0;
    const visibleTo = posts.length > 0 ? offset + posts.length : 0;

    const response = jsonResponse({
      target,
      source,
      subject_options: normalizeSubjectOptions(summary?.subject_options_json),
      summary: {
        total_posts: totalPosts,
        filtered_posts: filteredPosts,
        latest_seen_at: summary?.latest_seen_at ?? "",
        exported_posts: posts.length,
        recent_runs: runs.length,
      },
      pagination: {
        page,
        page_size: pageSize,
        total_pages: totalPages,
        visible_from: visibleFrom,
        visible_to: visibleTo,
        has_previous: page > 1,
        has_next: page < totalPages,
      },
      runs,
      posts,
    });
    if (edgeCache) {
      const cacheWrite = edgeCache.put(cacheKey, response.clone());
      if (typeof context.waitUntil === "function") {
        context.waitUntil(cacheWrite);
      } else {
        await cacheWrite;
      }
    }
    return response;
  } catch (error) {
    console.error("Archive API failed", error);
    return jsonResponse({ error: "Failed to load archive data from D1." }, 500);
  }
}
