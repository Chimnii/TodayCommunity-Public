const DEFAULT_TARGET = "dcinside-singularity";
const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 30;
const MAX_PAGE_SIZE = 100;
const MAX_SEARCH_LENGTH = 100;
const MAX_SUBJECT_LENGTH = 100;
const MAX_SUBJECT_OPTIONS = 100;

const SORT_COLUMNS = {
  created_at: "created_at",
  upvotes: "upvotes",
  comments: "comments",
};

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=UTF-8",
      "cache-control": "no-store",
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

export async function onRequestGet(context) {
  try {
    const url = new URL(context.request.url);
    const target = (url.searchParams.get("target") || DEFAULT_TARGET).trim();
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
        SELECT source_key, run_type, status, scanned_pages, scanned_posts, matched_posts, started_at, finished_at, error_message
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

    const runs = runResult.results ?? [];
    const posts = postResult.results ?? [];
    const totalPosts = normalizeCount(summary?.total_posts);
    const visibleFrom = posts.length > 0 ? offset + 1 : 0;
    const visibleTo = posts.length > 0 ? offset + posts.length : 0;

    return jsonResponse({
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
  } catch (error) {
    return jsonResponse(
      {
        error: "Failed to load archive data from D1.",
        details: String(error),
      },
      500
    );
  }
}
