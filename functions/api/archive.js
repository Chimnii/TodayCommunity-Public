const DEFAULT_TARGET = "dcinside-singularity";
const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 30;
const MAX_PAGE_SIZE = 100;
const MAX_SEARCH_LENGTH = 100;
const MAX_SUBJECT_LENGTH = 100;
const MAX_SUBJECT_OPTIONS = 100;
const MAX_TOPIC_ITEMS = 6;
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
      : "public, max-age=15, s-maxage=300";
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

function buildPostFilter(target, query, minUpvotes, minComments, subject, topicId) {
  const clauses = ["archive_key = ?", "status = 'active'"];
  const bindings = [target];

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

function buildOrderClause(sort) {
  const primaryColumn = SORT_COLUMNS[sort] ?? SORT_COLUMNS.created_at;
  if (primaryColumn === "created_at") {
    return "created_at DESC, id DESC";
  }
  return `${primaryColumn} DESC, created_at DESC, id DESC`;
}

function buildCacheKey(
  url,
  { target, pageSize, requestedPage, query, minUpvotes, minComments, subject, topicId, sort }
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
    topic: topicId ? String(topicId) : "",
    sort,
  };

  for (const [name, value] of Object.entries(normalized)) {
    if (value) {
      cacheUrl.searchParams.set(name, value);
    }
  }
  return new Request(cacheUrl.toString(), { method: "GET" });
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

function publicArchive(row) {
  if (!row) {
    return null;
  }

  return {
    archive_key: row.archive_key,
    display_name: row.display_name,
    description: row.description ?? "",
    content_kind: row.content_kind === "article" ? "article" : "community",
    display_order: Number(row.display_order ?? 0),
    updated_at: row.updated_at ?? "",
  };
}

function firstResult(result) {
  return result?.results?.[0] ?? null;
}

export async function onRequestGet(context) {
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
    const requestedSort = url.searchParams.get("sort") || "created_at";
    const sort = Object.hasOwn(SORT_COLUMNS, requestedSort) ? requestedSort : "created_at";
    const filter = buildPostFilter(
      target,
      query,
      minUpvotes,
      minComments,
      subject,
      topicId
    );
    const cacheKey = buildCacheKey(url, {
      target,
      pageSize,
      requestedPage,
      query,
      minUpvotes,
      minComments,
      subject,
      topicId,
      sort,
    });
    const cacheable = target !== "game-news";
    const edgeCache = cacheable ? globalThis.caches?.default : null;
    if (edgeCache) {
      const cached = await edgeCache.match(cacheKey);
      if (cached) {
        return cached;
      }
    }

    const db = context.env.DB;
    const archive = publicArchive(
      await db
        .prepare(
          `
          SELECT archive_key, display_name, description, content_kind,
                 display_order, updated_at
          FROM archives
          WHERE archive_key = ? AND is_public = 1
          LIMIT 1
          `
        )
        .bind(target)
        .first()
    );
    if (!archive) {
      return jsonResponse({ error: "Unknown archive target." }, 400);
    }
    const communityArchive = archive.content_kind !== "article";
    if (!communityArchive && topicId > 0) {
      return jsonResponse({ error: "Topic filters are unavailable for this archive." }, 400);
    }

    const batchStatements = [
      db.prepare(
        `
        SELECT archive_key, display_name, description, content_kind,
               display_order, updated_at
        FROM archives
        WHERE is_public = 1
        ORDER BY display_order ASC, archive_key ASC
        `
      ),
      db
        .prepare(
          `
          SELECT source_key, archive_key, site_name, board_name, board_url,
                 min_upvotes, min_comments, updated_at
          FROM sources
          WHERE archive_key = ?
          ORDER BY source_key ASC
          `
        )
        .bind(target),
      db
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
                WHERE archive_key = ?
                  AND status = 'active'
                  AND TRIM(subject) <> ''
                  AND length(TRIM(subject)) <= ${MAX_SUBJECT_LENGTH}
                ORDER BY subject COLLATE NOCASE, subject
                LIMIT ${MAX_SUBJECT_OPTIONS}
              )
            ) AS subject_options_json
          FROM posts
          WHERE archive_key = ?
            AND status = 'active'
          `
        )
        .bind(target, target),
      db
        .prepare(
          `
          SELECT COUNT(*) AS filtered_posts
          FROM posts
          WHERE ${filter.sql}
          `
        )
        .bind(...filter.bindings),
      db
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
        .bind(target),
    ];
    let selectedTopicIndex = -1;
    let topicSnapshotIndex = -1;
    let topicItemsIndex = -1;
    if (communityArchive) {
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
      topicSnapshotIndex = batchStatements.length;
      batchStatements.push(
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
          .bind(target)
      );
      topicItemsIndex = batchStatements.length;
      batchStatements.push(
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
          .bind(target)
      );
    }
    let requestedPostIndex = -1;
    if (requestedPage === 1) {
      requestedPostIndex = batchStatements.length;
      batchStatements.push(
        db
          .prepare(
            `
            SELECT archive_key, source_key, external_post_id, subject, title, post_url,
                   created_at, created_at_raw, upvotes, comments, qualifies_by,
                   fetched_at, first_seen_at, last_seen_at, status
            FROM posts
            WHERE ${filter.sql}
            ORDER BY ${buildOrderClause(sort)}
            LIMIT ? OFFSET ?
            `
          )
          .bind(...filter.bindings, pageSize, 0)
      );
    }

    const batchResults = await db.batch(batchStatements);
    const archiveResult = batchResults[0];
    const sourceResult = batchResults[1];
    const summaryResult = batchResults[2];
    const filteredSummaryResult = batchResults[3];
    const runResult = batchResults[4];
    const requestedPostResult = requestedPostIndex >= 0
      ? batchResults[requestedPostIndex]
      : null;
    const selectedTopicResult = selectedTopicIndex >= 0
      ? batchResults[selectedTopicIndex]
      : null;
    const topicSnapshotResult = topicSnapshotIndex >= 0
      ? batchResults[topicSnapshotIndex]
      : null;
    const topicItemsResult = topicItemsIndex >= 0
      ? batchResults[topicItemsIndex]
      : null;

    const archives = (archiveResult.results ?? []).map(publicArchive);
    const sources = sourceResult.results ?? [];
    const summary = firstResult(summaryResult);
    const filteredSummary = firstResult(filteredSummaryResult);
    const filteredPosts = normalizeCount(filteredSummary?.filtered_posts);
    const selectedTopic = firstResult(selectedTopicResult);
    if (topicId > 0 && !selectedTopic) {
      return jsonResponse({ error: "Unknown topic filter." }, 400);
    }
    const totalPages = Math.ceil(filteredPosts / pageSize);
    const page = Math.min(requestedPage, Math.max(totalPages, 1));
    const offset = (page - 1) * pageSize;

    let postResult = requestedPostResult;
    if (!postResult) {
      postResult = await db
        .prepare(
          `
          SELECT archive_key, source_key, external_post_id, subject, title, post_url,
                 created_at, created_at_raw, upvotes, comments, qualifies_by,
                 fetched_at, first_seen_at, last_seen_at, status
          FROM posts
          WHERE ${filter.sql}
          ORDER BY ${buildOrderClause(sort)}
          LIMIT ? OFFSET ?
          `
        )
        .bind(...filter.bindings, pageSize, offset)
        .all();
    }

    const runs = (runResult.results ?? []).map(
      ({ had_error: hadError, error_message: _discardedError, ...run }) => ({
        ...run,
        error_message: Number(hadError)
          ? "수집 처리 중 오류가 발생했습니다."
          : null,
      })
    );
    const posts = (postResult.results ?? []).map((post) => (
      target === "game-news"
        ? { ...post, feedback_key: String(post.external_post_id || "") }
        : post
    ));
    const totalPosts = normalizeCount(summary?.total_posts);
    const visibleFrom = posts.length > 0 ? offset + 1 : 0;
    const visibleTo = posts.length > 0 ? offset + posts.length : 0;

    const response = jsonResponse({
      target,
      archives,
      archive,
      sources,
      source: sources[0] ?? null,
      subject_options: normalizeSubjectOptions(summary?.subject_options_json),
      selected_topic: selectedTopic
        ? {
            topic_id: Number(selectedTopic.topic_id),
            label: String(selectedTopic.label ?? ""),
          }
        : null,
      topic_trends: communityArchive
        ? publicTopicTrends(
            firstResult(topicSnapshotResult),
            topicItemsResult?.results ?? []
          )
        : null,
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
    }, 200, cacheable);
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
