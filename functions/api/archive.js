const DEFAULT_TARGET = "dcinside-singularity";
const ALL_TARGET = "all";
const ALL_ARCHIVE = Object.freeze({
  archive_key: ALL_TARGET,
  display_name: "모두",
  description: "모든 공개 아카이브의 글",
  content_kind: "mixed",
  display_order: 100,
  updated_at: "",
});
const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 30;
const MAX_PAGE_SIZE = 100;
const MAX_SEARCH_LENGTH = 100;
const MAX_LIKE_PATTERN_BYTES = 50;
const MAX_SUBJECT_LENGTH = 100;
const MAX_SUBJECT_OPTIONS = 100;
const MAX_ARCHIVE_FILTER_KEYS = 50;
const MAX_TOPIC_ITEMS = 6;
const MAX_CURSOR_LENGTH = 8192;
const QUICK_PAGE_COUNT = 5;
const MAX_SCAN_ROWS = 512;
const TARGET_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;
const TOPIC_TREND_STATES = new Set(["new", "rising", "active"]);

const SORT_COLUMNS = {
  created_at: "created_at",
  upvotes: "upvotes",
  comments: "comments",
};

function jsonResponse(body, status = 200, cacheable = true) {
  const cacheControl =
    status >= 400 || !cacheable
      ? "no-store"
      : "public, max-age=15, s-maxage=120";
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: {
      "content-type": "application/json; charset=UTF-8",
      "cache-control": cacheControl,
      "x-content-type-options": "nosniff",
    },
  });
}

async function storePublicCache(cache, key, response, context) {
  const write = (async () => {
    try { await cache.put(key, response); }
    catch { /* An optional cache write must not fail a successful DB response. */ }
  })();
  if (typeof context.waitUntil === "function") context.waitUntil(write);
  else await write;
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
  const value = (rawValue ?? "").trim();
  return Array.from(value).length <= MAX_SEARCH_LENGTH &&
    new TextEncoder().encode(`%${escapeLike(value)}%`).length <= MAX_LIKE_PATTERN_BYTES
    ? value : null;
}

function normalizeSubject(rawValue) {
  const characters = Array.from((rawValue ?? "").trim());
  return characters.length <= MAX_SUBJECT_LENGTH ? characters.join("") : null;
}

function normalizeTopicId(rawValue) {
  if (rawValue === null || rawValue === undefined || rawValue === "") {
    return 0;
  }
  const value = String(rawValue).trim();
  if (!/^\d+$/.test(value)) {
    return null;
  }
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : null;
}

function normalizeExcludedArchives(searchParams) {
  const rawKeys = searchParams.getAll("exclude_archive");
  if (rawKeys.length > MAX_ARCHIVE_FILTER_KEYS) {
    return null;
  }
  const keys = [];
  const seen = new Set();
  for (const rawKey of rawKeys) {
    const key = String(rawKey || "").trim();
    if (key === ALL_TARGET || !TARGET_PATTERN.test(key)) {
      return null;
    }
    if (!seen.has(key)) {
      seen.add(key);
      keys.push(key);
    }
  }
  return keys;
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

function buildPostFilter(
  target,
  query,
  minUpvotes,
  minComments,
  subject,
  topicId,
  excludedArchives
) {
  let clauses;
  let bindings;
  if (target === ALL_TARGET && excludedArchives.length > 0) {
    const placeholders = excludedArchives.map(() => "?").join(", ");
    clauses = [
      `EXISTS (
        SELECT 1
        FROM archives AS public_archive
        WHERE public_archive.archive_key = posts.archive_key
          AND public_archive.is_public = 1
          AND public_archive.archive_key NOT IN (${placeholders})
      )`,
      "status = 'active'",
    ];
    bindings = [...excludedArchives];
  } else if (target === ALL_TARGET) {
    clauses = [
      `EXISTS (
        SELECT 1
        FROM archives AS public_archive
        WHERE public_archive.archive_key = posts.archive_key
          AND public_archive.is_public = 1
      )`,
      "status = 'active'",
    ];
    bindings = [];
  } else {
    clauses = ["archive_key = ?", "status = 'active'"];
    bindings = [target];
  }

  if (minUpvotes > 0) {
    clauses.push("upvotes >= ?");
    bindings.push(minUpvotes);
  }

  if (minComments > 0) {
    clauses.push("comments >= ?");
    bindings.push(minComments);
  }

  if (subject) {
    clauses.push("subject = ?");
    bindings.push(subject);
  }

  if (query) {
    clauses.push("title LIKE ? ESCAPE '\\'");
    bindings.push(`%${escapeLike(query)}%`);
  }

  if (topicId > 0) {
    clauses.push(`
      EXISTS (
        SELECT 1
        FROM community_post_topics AS selected_post_topic
        INNER JOIN community_topics AS selected_topic
          ON selected_topic.id = selected_post_topic.topic_id
        WHERE selected_post_topic.post_id = posts.id
          AND selected_post_topic.topic_id = ?
          AND selected_topic.archive_key = posts.archive_key
      )
    `);
    bindings.push(topicId);
  }

  return {
    sql: clauses.join("\n          AND "),
    bindings,
  };
}

function buildOrderClause(sort, direction = "next") {
  const primaryColumn = SORT_COLUMNS[sort] ?? SORT_COLUMNS.created_at;
  const order = direction === "previous" ? "ASC" : "DESC";
  if (primaryColumn === "created_at") {
    return `created_at ${order}, id ${order}`;
  }
  return `${primaryColumn} ${order}, created_at ${order}, id ${order}`;
}

function buildCacheKey(
  url,
  {
    target,
    pageSize,
    requestedPage,
    query,
    minUpvotes,
    minComments,
    subject,
    topicId,
    sort,
    excludedArchives,
    cursor,
  }
) {
  const cacheUrl = new URL(url.pathname, url.origin);
  const normalized = {
    format: "quick-pages-v3",
    target,
    page: String(requestedPage),
    page_size: String(pageSize),
    q: query,
    min_upvotes: String(minUpvotes),
    min_comments: String(minComments),
    subject,
    topic: topicId ? String(topicId) : "",
    sort,
    cursor,
  };

  for (const [name, value] of Object.entries(normalized)) {
    if (value) {
      cacheUrl.searchParams.set(name, value);
    }
  }
  for (const archiveKey of [...excludedArchives].sort()) {
    cacheUrl.searchParams.append("exclude_archive", archiveKey);
  }
  return new Request(cacheUrl.toString(), { method: "GET" });
}

function hasPostFilters({ query, minUpvotes, minComments, subject, topicId }) {
  return Boolean(query || minUpvotes > 0 || minComments > 0 || subject || topicId > 0);
}

function selectStatsRows(rows, target, excludedArchives) {
  const excluded = new Set(excludedArchives);
  if (target === ALL_TARGET) {
    return rows.filter((row) => !excluded.has(String(row.archive_key || "")));
  }
  return rows.filter((row) => row.archive_key === target);
}

function summarizeStats(rows) {
  let totalPosts = 0;
  let latestSeenAt = "";
  const versions = [];
  const subjectValues = [];

  for (const row of rows) {
    totalPosts += normalizeCount(row.active_post_count);
    const latest = String(row.latest_seen_at || "");
    if (latest > latestSeenAt) {
      latestSeenAt = latest;
    }
    versions.push(`${row.archive_key}:${normalizeCount(row.stats_version)}`);
    subjectValues.push(...normalizeSubjectOptions(row.subject_options_json));
  }

  return {
    totalPosts,
    latestSeenAt,
    statsVersion: versions.sort().join(",") || "empty:0",
    subjectOptions: normalizeSubjectOptions(subjectValues),
  };
}

function cursorFilterSignature({
  target,
  pageSize,
  query,
  minUpvotes,
  minComments,
  subject,
  topicId,
  sort,
  excludedArchives,
}) {
  return JSON.stringify({
    target,
    pageSize,
    query,
    minUpvotes,
    minComments,
    subject,
    topicId,
    sort,
    excludedArchives: [...excludedArchives].sort(),
  });
}

function encodeCursor(payload) {
  const bytes = new TextEncoder().encode(JSON.stringify(payload));
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function decodeCursor(rawValue, expectedSignature, sort) {
  const raw = String(rawValue || "").trim();
  if (!raw) {
    return null;
  }
  if (raw.length > MAX_CURSOR_LENGTH || !/^[A-Za-z0-9_-]+$/.test(raw)) {
    return false;
  }

  try {
    const padded = raw.replace(/-/g, "+").replace(/_/g, "/")
      .padEnd(Math.ceil(raw.length / 4) * 4, "=");
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const payload = JSON.parse(new TextDecoder().decode(bytes));
    const expectedKeyLength = sort === "created_at" ? 2 : 3;
    if (
      payload?.version !== 1 ||
      !["next", "previous"].includes(payload.direction) ||
      payload.signature !== expectedSignature ||
      payload.sort !== sort ||
      !Number.isSafeInteger(payload.page) ||
      payload.page < 1 ||
      !Array.isArray(payload.key) ||
      payload.key.length !== expectedKeyLength ||
      (payload.inclusive !== undefined && typeof payload.inclusive !== "boolean")
    ) {
      return false;
    }

    const id = Number(payload.key.at(-1));
    const createdAt = String(payload.key.at(-2) || "");
    const metric = sort === "created_at" ? null : Number(payload.key[0]);
    if (
      !Number.isSafeInteger(id) ||
      id < 1 ||
      !createdAt ||
      createdAt.length > 40 ||
      (sort !== "created_at" && !Number.isSafeInteger(metric))
    ) {
      return false;
    }
    return {
      direction: payload.direction,
      page: payload.page,
      key: sort === "created_at" ? [createdAt, id] : [metric, createdAt, id],
      inclusive: payload.inclusive === true,
    };
  } catch {
    return false;
  }
}

function buildCursorBoundary(sort, cursor) {
  if (!cursor) {
    return { sql: "", bindings: [] };
  }
  const operator = (cursor.direction === "previous" ? ">" : "<") + (cursor.inclusive ? "=" : "");
  if (sort === "created_at") {
    const [createdAt, id] = cursor.key;
    return {
      sql: `(posts.created_at, posts.id) ${operator} (?, ?)`,
      bindings: [createdAt, id],
    };
  }

  const [metric, createdAt, id] = cursor.key;
  const column = SORT_COLUMNS[sort];
  return {
    sql: `(
      posts.${column}, posts.created_at, posts.id
    ) ${operator} (?, ?, ?)`,
    bindings: [metric, createdAt, id],
  };
}

function cursorKeyForRow(row, sort) {
  const id = Number(row.cursor_id);
  const createdAt = String(row.created_at || "");
  return sort === "created_at"
    ? [createdAt, id]
    : [Number(row[SORT_COLUMNS[sort]]), createdAt, id];
}

function makeCursor(direction, page, row, sort, signature, inclusive = false) {
  return encodeCursor({
    version: 1,
    direction,
    page,
    sort,
    signature,
    key: cursorKeyForRow(row, sort),
    ...(inclusive ? { inclusive: true } : {}),
  });
}

function parseJsonArray(value) {
  try {
    const parsed = Array.isArray(value) ? value : JSON.parse(value || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function publicTopicTrends(snapshot, itemRows) {
  if (!snapshot) {
    return null;
  }
  const topics = [];
  for (const row of itemRows ?? []) {
    const topicId = Number(row.topic_id);
    const label = String(row.label ?? "").trim();
    if (!Number.isSafeInteger(topicId) || topicId < 1 || !label) {
      continue;
    }
    const trendState = TOPIC_TREND_STATES.has(row.trend_state)
      ? row.trend_state
      : "active";
    const hotnessScore = Number(row.hotness_score ?? 0);
    const representativePosts = parseJsonArray(row.representative_posts_json)
      .filter((post) => post && typeof post === "object")
      .slice(0, 2)
      .map((post) => ({
        external_post_id: String(post.external_post_id ?? ""),
        title: String(post.title ?? ""),
        post_url: String(post.post_url ?? ""),
        created_at: String(post.created_at ?? ""),
      }));
    topics.push({
      topic_id: topicId,
      label,
      post_count: normalizeCount(row.post_count),
      previous_post_count: normalizeCount(row.previous_post_count),
      hotness_score: Number.isFinite(hotnessScore) && hotnessScore >= 0
        ? hotnessScore
        : 0,
      trend_state: trendState,
      representative_posts: representativePosts,
    });
  }
  return {
    window_hours: normalizeCount(snapshot.window_hours),
    window_start: snapshot.window_start ?? "",
    window_end: snapshot.window_end ?? "",
    generated_at: snapshot.generated_at ?? "",
    summary: snapshot.summary_text ?? "",
    eligible_post_count: normalizeCount(snapshot.eligible_post_count),
    analyzed_post_count: normalizeCount(snapshot.analyzed_post_count),
    topics: topics.slice(0, MAX_TOPIC_ITEMS),
  };
}

function publicLatestTopicTrends(row) {
  if (!row) {
    return null;
  }
  let payload;
  try {
    payload = JSON.parse(String(row.payload_json ?? ""));
  } catch {
    throw new Error("Latest topic snapshot payload is invalid JSON.");
  }
  if (
    !payload ||
    typeof payload !== "object" ||
    Array.isArray(payload) ||
    payload.version !== 1 ||
    !Array.isArray(payload.topics)
  ) {
    throw new Error("Latest topic snapshot payload has an unsupported shape.");
  }
  return publicTopicTrends(
    {
      window_hours: payload.window_hours,
      window_start: payload.window_start,
      window_end: payload.window_end,
      generated_at: payload.generated_at,
      summary_text: payload.summary,
      eligible_post_count: payload.eligible_post_count,
      analyzed_post_count: payload.analyzed_post_count,
    },
    payload.topics.map((topic) => ({
      ...topic,
      representative_posts_json: topic?.representative_posts,
    }))
  );
}

async function loadLegacyTopicTrends(db, archiveKey) {
  const [snapshotResult, itemsResult] = await db.batch([
    db
      .prepare(
        `
        SELECT window_start, window_end, window_hours, generated_at,
               summary_text, eligible_post_count, analyzed_post_count
        FROM community_topic_snapshots
        WHERE archive_key = ?
        ORDER BY generated_at DESC, id DESC
        LIMIT 1
        `
      )
      .bind(archiveKey),
    db
      .prepare(
        `
        SELECT item.topic_id, topic.label, item.topic_rank, item.post_count,
               item.previous_post_count, item.hotness_score, item.trend_state,
               COALESCE(
                 (
                   SELECT json_group_array(
                     json_object(
                       'external_post_id', representative.external_post_id,
                       'title', representative.title,
                       'post_url', representative.post_url,
                       'created_at', representative.created_at
                     )
                   )
                   FROM (
                     SELECT post.external_post_id, post.title, post.post_url,
                            post.created_at
                     FROM community_topic_snapshot_representatives AS link
                     INNER JOIN posts AS post ON post.id = link.post_id
                     WHERE link.snapshot_id = item.snapshot_id
                       AND link.topic_id = item.topic_id
                       AND post.archive_key = topic.archive_key
                       AND post.status = 'active'
                     ORDER BY link.representative_rank ASC
                     LIMIT 2
                   ) AS representative
                 ),
                 '[]'
               ) AS representative_posts_json
        FROM community_topic_snapshot_items AS item
        INNER JOIN community_topics AS topic ON topic.id = item.topic_id
        WHERE item.snapshot_id = (
          SELECT id
          FROM community_topic_snapshots
          WHERE archive_key = ?
          ORDER BY generated_at DESC, id DESC
          LIMIT 1
        )
        ORDER BY item.topic_rank ASC
        LIMIT ${MAX_TOPIC_ITEMS}
        `
      )
      .bind(archiveKey),
  ]);
  return publicTopicTrends(
    firstResult(snapshotResult),
    itemsResult?.results ?? []
  );
}

function publicArchive(row) {
  if (!row) {
    return null;
  }

  return {
    archive_key: row.archive_key,
    display_name: row.display_name,
    description: row.description ?? "",
    content_kind: ["article", "mixed"].includes(row.content_kind)
      ? row.content_kind
      : "community",
    display_order: Number(row.display_order ?? 0),
    updated_at: row.updated_at ?? "",
  };
}

function firstResult(result) {
  return result?.results?.[0] ?? null;
}

function meteredDatabase(binding, usage) {
  const record = (result) => {
    usage.statements += 1;
    const meta = result?.meta;
    if (!Number.isFinite(meta?.rows_read) || !Number.isFinite(meta?.rows_written)) {
      usage.incomplete_meta += 1;
    } else {
      usage.rows_read += meta.rows_read;
      usage.rows_written += meta.rows_written;
    }
    return result;
  };
  const wrap = (statement) => ({
    statement,
    bind(...values) { return wrap(statement.bind(...values)); },
    async all() { return record(await statement.all()); },
  });
  return {
    prepare(sql) { return wrap(binding.prepare(sql)); },
    async batch(statements) {
      return (await binding.batch(statements.map((item) => item.statement))).map(record);
    },
  };
}

export async function onRequestGet(context) {
  const usage = { endpoint: "archive", cache: "unresolved", statements: 0,
    rows_read: 0, rows_written: 0, incomplete_meta: 0, outcome: "ok" };
  try {
    const url = new URL(context.request.url);
    const target = (url.searchParams.get("target") || DEFAULT_TARGET).trim();
    if (!TARGET_PATTERN.test(target)) {
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
    if (query === null) {
      return jsonResponse({ error: "검색어가 너무 깁니다. 한글 기준 16자 이내로 줄여 주세요.", code: "search_too_long" }, 400);
    }
    const minUpvotes = normalizeMinimum(url.searchParams.get("min_upvotes"));
    const minComments = normalizeMinimum(url.searchParams.get("min_comments"));
    const subject = normalizeSubject(url.searchParams.get("subject"));
    if (subject === null) {
      return jsonResponse({ error: "Subject filter must be 100 characters or fewer." }, 400);
    }
    const topicId = normalizeTopicId(url.searchParams.get("topic"));
    if (topicId === null) {
      return jsonResponse({ error: "Topic filter must be a positive integer." }, 400);
    }
    const allArchives = target === ALL_TARGET;
    const excludedArchives = normalizeExcludedArchives(url.searchParams);
    if (excludedArchives === null) {
      return jsonResponse({ error: "Archive filters are invalid." }, 400);
    }
    if (!allArchives && excludedArchives.length > 0) {
      return jsonResponse({ error: "Archive filters require the all target." }, 400);
    }
    if (allArchives && topicId > 0) {
      return jsonResponse({ error: "Topic filters are unavailable for this archive." }, 400);
    }
    const requestedSort = url.searchParams.get("sort") || "created_at";
    const sort = Object.hasOwn(SORT_COLUMNS, requestedSort) ? requestedSort : "created_at";
    const filter = buildPostFilter(
      target,
      query,
      minUpvotes,
      minComments,
      subject,
      topicId,
      excludedArchives
    );
    const filteredMode = hasPostFilters({
      query,
      minUpvotes,
      minComments,
      subject,
      topicId,
    });
    const signature = cursorFilterSignature({
      target,
      pageSize,
      query,
      minUpvotes,
      minComments,
      subject,
      topicId,
      sort,
      excludedArchives,
    });
    const rawCursor = String(url.searchParams.get("cursor") || "").trim();
    const cursor = decodeCursor(rawCursor, signature, sort);
    if (cursor === false || (cursor && cursor.page > maxPage)) {
      return jsonResponse({ error: "Archive cursor is invalid or expired." }, 400);
    }

    // This endpoint contains only public data. Personal feedback and auth stay
    // in their separate no-store endpoints; cookies do not vary this response.
    const bypassCache = context.request.headers.get("x-tc-refresh") === "1" ||
      /(?:no-cache|no-store)/i.test(context.request.headers.get("cache-control") || "");
    const cacheable = !bypassCache;
    const edgeCache = cacheable ? globalThis.caches?.default : null;
    const cacheKey = buildCacheKey(url, {
      target,
      pageSize,
      requestedPage: cursor?.page ?? (filteredMode ? 1 : requestedPage),
      query,
      minUpvotes,
      minComments,
      subject,
      topicId,
      sort,
      excludedArchives,
      cursor: rawCursor,
    });
    usage.cache = bypassCache ? "bypass" : "miss";
    if (edgeCache) {
      let cached = null;
      try { cached = await edgeCache.match(cacheKey); }
      catch { /* A cache outage falls back to D1. */ }
      if (cached) {
        usage.cache = "hit";
        const response = new Response(cached.body, cached);
        response.headers.set("x-tc-cache", "hit");
        return response;
      }
    }

    const db = meteredDatabase(context.env.DB, usage);
    const statsResult = await db
      .prepare(
        `
        SELECT archive.archive_key, archive.display_name, archive.description,
               archive.content_kind, archive.display_order, archive.updated_at,
               COALESCE(stats.active_post_count, 0) AS active_post_count,
               COALESCE(stats.latest_seen_at, '') AS latest_seen_at,
               COALESCE(stats.stats_version, 0) AS stats_version,
               COALESCE(stats.subject_options_json, '[]') AS subject_options_json
        FROM archives AS archive
        LEFT JOIN archive_stats AS stats
          ON stats.archive_key = archive.archive_key
        WHERE archive.is_public = 1
        ORDER BY archive.display_order ASC, archive.archive_key ASC
        `
      )
      .all();
    const publicStatsRows = statsResult.results ?? [];
    const selectedArchiveRow = allArchives
      ? null
      : publicStatsRows.find((row) => row.archive_key === target);
    const archive = allArchives ? ALL_ARCHIVE : publicArchive(selectedArchiveRow);
    if (!archive) {
      return jsonResponse({ error: "Unknown archive target." }, 400);
    }
    const relevantStatsRows = selectStatsRows(
      publicStatsRows,
      target,
      excludedArchives
    );
    const statsSummary = summarizeStats(relevantStatsRows);
    const totalPosts = statsSummary.totalPosts;
    const exactTotalPages = Math.ceil(totalPosts / pageSize);
    const sequentialMode = filteredMode || allArchives || Boolean(cursor) || exactTotalPages > QUICK_PAGE_COUNT;
    const totalPages = sequentialMode ? null : exactTotalPages;
    const page = cursor?.page ?? (filteredMode ? 1
      : Math.min(requestedPage, Math.max(exactTotalPages, 1)));
    if (sequentialMode && !filteredMode && !cursor && page > QUICK_PAGE_COUNT && page !== exactTotalPages) {
      return jsonResponse({ error: "1~5페이지는 바로 이동할 수 있습니다. 그 이후는 이전·다음으로 이동해 주세요.", code: "deep_page_requires_cursor" }, 400);
    }
    // Numeric jumps on small archives seek from the nearest end; large archives
    // and sparse filters use bounded candidate windows and tuple cursors.
    const offset = (page - 1) * pageSize;
    const reverseOffset = Math.max(0, totalPosts - offset - pageSize);
    const reversePage = !cursor && !filteredMode && page > 1 && reverseOffset < offset;
    const direction = cursor?.direction ?? (reversePage ? "previous" : "next");
    const visibleLimit = reversePage ? Math.min(pageSize, Math.max(0, totalPosts - offset)) : pageSize;
    const shallowSkip = sequentialMode && !filteredMode && !cursor && !reversePage ? offset : 0;
    const candidateLimit = filteredMode ? MAX_SCAN_ROWS : shallowSkip + visibleLimit + 1;
    const communityArchive = archive.content_kind === "community";
    if (!communityArchive && topicId > 0) {
      return jsonResponse({ error: "Topic filters are unavailable for this archive." }, 400);
    }

    // Share page-independent public metadata across sorts/pages. The catalog
    // comes from the fresh stats lookup, so a changed public archive set cannot
    // reuse metadata from an earlier visibility configuration.
    const metadataUrl = new URL("/api/archive-metadata", url.origin);
    metadataUrl.searchParams.set("format", "public-v1");
    metadataUrl.searchParams.set("target", target);
    metadataUrl.searchParams.set("catalog", JSON.stringify(publicStatsRows.map(row =>
      [row.archive_key, row.content_kind]).sort((a, b) => a[0].localeCompare(b[0]))));
    const metadataKey = new Request(metadataUrl.toString());
    let cachedMetadata = null;
    if (edgeCache) {
      try {
        const entry = await edgeCache.match(metadataKey);
        const value = entry ? await entry.json() : null;
        if (value && Array.isArray(value.sources) && Array.isArray(value.runs)
            && Number.isFinite(value.expires_at) && value.expires_at > Date.now()
            && value.expires_at <= Date.now() + 120_000) cachedMetadata = value;
      } catch { /* Metadata cache misses/failures fall back to the bounded DB queries. */ }
    }
    usage.metadata_cache = cachedMetadata ? "hit" : bypassCache ? "bypass" : "miss";

    const sourceStatement = allArchives
      ? db.prepare(
          `
          SELECT sources.source_key, sources.archive_key, sources.site_name,
                 sources.board_name, sources.board_url, sources.min_upvotes,
                 sources.min_comments, sources.updated_at
          FROM sources
          INNER JOIN archives AS source_archive
            ON source_archive.archive_key = sources.archive_key
          WHERE source_archive.is_public = 1
          ORDER BY source_archive.display_order ASC, sources.source_key ASC
          `
        )
      : db
          .prepare(
            `
            SELECT source_key, archive_key, site_name, board_name, board_url,
                   min_upvotes, min_comments, updated_at
            FROM sources
            WHERE archive_key = ?
            ORDER BY source_key ASC
            `
          )
          .bind(target);
    const runsStatement = allArchives
      ? db.prepare(
          `
          WITH archive_sources AS (
            SELECT sources.source_key, sources.site_name, sources.board_name
            FROM sources
            INNER JOIN archives AS source_archive
              ON source_archive.archive_key = sources.archive_key
            WHERE source_archive.is_public = 1
          )
          SELECT runs.source_key, sources.site_name, sources.board_name,
                 runs.run_type, runs.status, runs.scanned_pages, runs.scanned_posts,
                 runs.matched_posts, runs.started_at, runs.finished_at,
                 CASE
                   WHEN runs.status IN ('failed', 'blocked')
                    AND runs.error_message IS NOT NULL
                    AND TRIM(runs.error_message) <> ''
                   THEN 1
                   ELSE 0
                 END AS had_error
          FROM archive_sources AS sources
          INNER JOIN crawl_runs AS runs
            ON runs.id IN (
              SELECT source_runs.id
              FROM crawl_runs AS source_runs
              WHERE source_runs.source_key = sources.source_key
              ORDER BY source_runs.id DESC
              LIMIT 10
            )
          ORDER BY runs.id DESC
          LIMIT 10
          `
        )
      : db
          .prepare(
            `
            WITH archive_sources AS (
              SELECT source_key, site_name, board_name
              FROM sources
              WHERE archive_key = ?
            )
            SELECT runs.source_key, sources.site_name, sources.board_name,
                   runs.run_type, runs.status, runs.scanned_pages, runs.scanned_posts,
                   runs.matched_posts, runs.started_at, runs.finished_at,
                   CASE
                     WHEN runs.status IN ('failed', 'blocked')
                      AND runs.error_message IS NOT NULL
                      AND TRIM(runs.error_message) <> ''
                     THEN 1
                     ELSE 0
                   END AS had_error
            FROM archive_sources AS sources
            INNER JOIN crawl_runs AS runs
              ON runs.id IN (
                SELECT source_runs.id
                FROM crawl_runs AS source_runs
                WHERE source_runs.source_key = sources.source_key
                ORDER BY source_runs.id DESC
                LIMIT 10
              )
            ORDER BY runs.id DESC
            LIMIT 10
            `
          )
          .bind(target);
    const batchStatements = cachedMetadata ? [] : [sourceStatement, runsStatement];
    let selectedTopicIndex = -1;
    let topicLatestIndex = -1;
    if (communityArchive && topicId > 0) {
      selectedTopicIndex = batchStatements.length;
      batchStatements.push(
        db
          .prepare(
            `
            SELECT id AS topic_id, label
            FROM community_topics
            WHERE id = ? AND archive_key = ?
            LIMIT 1
            `
          )
          .bind(topicId, target)
      );
    }
    if (communityArchive && !cachedMetadata) {
      topicLatestIndex = batchStatements.length;
      batchStatements.push(
        db
          .prepare(
            `
            SELECT payload_json
            FROM community_topic_latest
            WHERE archive_key = ?
            LIMIT 1
            `
          )
          .bind(target)
      );
    }
    const requestedPostIndex = batchStatements.length;
    const cursorBoundary = buildCursorBoundary(sort, cursor);
    const postFilterSql = cursorBoundary.sql
      ? `${filter.sql}\n            AND ${cursorBoundary.sql}`
      : filter.sql;
    const columns = `archive_key, source_key, external_post_id, subject, title, post_url,
      created_at, created_at_raw, created_at_basis, created_at_precision,
      upvotes, comments, qualifies_by, fetched_at, first_seen_at, last_seen_at,
      status, id AS cursor_id`;
    if (sequentialMode) {
      const candidateBindings = [];
      // Seek each public archive independently before merging. Private and
      // excluded rows must never become an exposed continuation boundary.
      const candidateArchives = allArchives ? relevantStatsRows.map((row) => row.archive_key) : [target];
      const candidateQueries = candidateArchives.map((archiveKey) => {
        candidateBindings.push(archiveKey, ...cursorBoundary.bindings, archiveKey);
        return `SELECT * FROM (SELECT * FROM posts
          WHERE posts.status = 'active' AND posts.archive_key = ?
          ${cursorBoundary.sql ? `AND ${cursorBoundary.sql}` : ""}
          AND EXISTS (SELECT 1 FROM archives WHERE archive_key = ? AND is_public = 1)
          ORDER BY ${buildOrderClause(sort, direction)} LIMIT ${candidateLimit})`;
      });
      const candidateSql = candidateQueries.length
        ? candidateQueries.join(" UNION ALL ")
        : "SELECT * FROM posts WHERE 0";
      batchStatements.push(db.prepare(`
        WITH candidates AS MATERIALIZED (
          SELECT * FROM (${candidateSql})
          ORDER BY ${buildOrderClause(sort, direction)} LIMIT ${candidateLimit}
        )
        SELECT ${columns}, CASE WHEN ${filter.sql} THEN 1 ELSE 0 END AS matches_filter
        FROM candidates AS posts
        ORDER BY ${buildOrderClause(sort, direction)}
      `).bind(...candidateBindings, ...filter.bindings));
    } else {
      batchStatements.push(db.prepare(`
        SELECT ${columns} FROM posts WHERE ${postFilterSql}
        AND EXISTS (SELECT 1 FROM archives WHERE archive_key = posts.archive_key AND is_public = 1)
        ORDER BY ${buildOrderClause(sort, direction)} LIMIT ? OFFSET ?
      `).bind(...filter.bindings, ...cursorBoundary.bindings,
        visibleLimit, cursor ? 0 : reversePage ? reverseOffset : offset));
    }

    const batchResults = await db.batch(batchStatements);
    const sourceResult = batchResults[0];
    const runResult = batchResults[1];
    const requestedPostResult = batchResults[requestedPostIndex];
    const selectedTopicResult = selectedTopicIndex >= 0
      ? batchResults[selectedTopicIndex]
      : null;
    const topicLatestResult = topicLatestIndex >= 0
      ? batchResults[topicLatestIndex]
      : null;

    const archives = [
      ...publicStatsRows
        .map(publicArchive)
        .filter((candidate) => candidate?.archive_key !== ALL_TARGET),
      ALL_ARCHIVE,
    ];
    const sources = cachedMetadata?.sources ?? sourceResult.results ?? [];
    const selectedTopic = firstResult(selectedTopicResult);
    if (topicId > 0 && !selectedTopic) {
      return jsonResponse({ error: "Unknown topic filter." }, 400);
    }
    let topicTrends = cachedMetadata ? cachedMetadata.topic_trends : communityArchive
      ? publicLatestTopicTrends(firstResult(topicLatestResult))
      : null;
    if (!cachedMetadata && communityArchive && !topicTrends) {
      topicTrends = await loadLegacyTopicTrends(db, target);
    }
    const runs = cachedMetadata?.runs ?? (runResult.results ?? []).map(
      ({ had_error: hadError, error_message: _discardedError, ...run }) => ({
        ...run,
        error_message: Number(hadError)
          ? "수집 처리 중 오류가 발생했습니다."
          : null,
      })
    );
    if (!cachedMetadata && edgeCache) {
      const metadata = { sources, runs, topic_trends: topicTrends, expires_at: Date.now() + 120_000 };
      await storePublicCache(edgeCache, metadataKey, jsonResponse(metadata), context);
    }
    const postRows = (requestedPostResult.results ?? []).slice(shallowSkip);
    const matchingRows = sequentialMode
      ? postRows.filter((row) => Number(row.matches_filter ?? 1) === 1) : postRows;
    const hasExtraRow = matchingRows.length > visibleLimit;
    const queryRows = matchingRows.slice(0, visibleLimit);
    const scannedThrough = hasExtraRow ? queryRows.at(-1) : postRows.at(-1);
    const canContinue = hasExtraRow || postRows.length === candidateLimit;
    const visibleRows = direction === "previous" ? [...queryRows].reverse() : queryRows;
    const hasPrevious = sequentialMode
      ? direction === "previous" ? page > 1 && canContinue : page > 1 : page > 1;
    const hasNext = sequentialMode
      ? direction === "previous" ? !reversePage : canContinue : page < exactTotalPages;
    const exhaustedBoundary = cursor ? {
      cursor_id: cursor.key.at(-1), created_at: cursor.key.at(-2),
      ...(sort === "created_at" ? {} : { [sort]: cursor.key[0] }),
    } : null;
    const previousBoundary = (direction === "previous" ? scannedThrough : postRows[0]) ?? exhaustedBoundary;
    const nextBoundary = (direction === "previous" ? postRows[0] : scannedThrough) ?? exhaustedBoundary;
    const previousCursor = sequentialMode && hasPrevious && previousBoundary
      ? makeCursor("previous", page - 1, previousBoundary, sort, signature, postRows.length === 0) : null;
    const nextCursor = sequentialMode && hasNext && nextBoundary
      ? makeCursor("next", page + 1, nextBoundary, sort, signature, postRows.length === 0) : null;
    const posts = visibleRows.map(({ cursor_id: _cursorId, matches_filter: _match, ...post }) => (
      post.archive_key === "game-news"
        ? { ...post, feedback_key: String(post.external_post_id || "") } : post
    ));
    const visibleFrom = posts.length > 0 ? offset + 1 : 0;
    const visibleTo = posts.length > 0 ? offset + posts.length : 0;

    const response = jsonResponse({
      target,
      archives,
      archive,
      sources,
      source: sources[0] ?? null,
      subject_options: statsSummary.subjectOptions,
      selected_topic: selectedTopic
        ? {
            topic_id: Number(selectedTopic.topic_id),
            label: String(selectedTopic.label ?? ""),
          }
        : null,
      topic_trends: topicTrends,
      summary: {
        total_posts: totalPosts,
        filtered_posts: filteredMode ? null : totalPosts,
        latest_seen_at: statsSummary.latestSeenAt,
        stats_version: statsSummary.statsVersion,
        exported_posts: posts.length,
        recent_runs: runs.length,
      },
      pagination: {
        mode: sequentialMode ? "sequential" : "numbered",
        ...(sequentialMode ? { bounded_scan: true, last_page: filteredMode ? null : exactTotalPages } : {}),
        page,
        page_size: pageSize,
        total_pages: totalPages,
        quick_page_count: filteredMode ? null : Math.min(QUICK_PAGE_COUNT, exactTotalPages),
        visible_from: visibleFrom,
        visible_to: visibleTo,
        has_previous: hasPrevious,
        has_next: hasNext,
        previous_cursor: previousCursor,
        next_cursor: nextCursor,
      },
      runs,
      posts,
    }, 200, cacheable);
    response.headers.set("x-tc-cache", usage.cache);
    if (cachedMetadata) {
      // Layering response caches must not extend metadata freshness to 240s.
      const remaining = Math.max(0, Math.floor((cachedMetadata.expires_at - Date.now()) / 1000));
      response.headers.set("cache-control", `public, max-age=${Math.min(15, remaining)}, s-maxage=${remaining}`);
    }
    if (edgeCache) {
      await storePublicCache(edgeCache, cacheKey, response.clone(), context);
    }
    return response;
  } catch (error) {
    usage.outcome = "failed";
    console.error("Archive API failed", { name: error?.name || "Error" });
    return jsonResponse({ error: "Failed to load archive data from D1." }, 500);
  } finally {
    console.log(JSON.stringify({ d1_api_usage: usage }));
  }
}
