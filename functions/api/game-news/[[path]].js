import {
  AuthConfigurationError,
  resolveAuthIdentity,
} from "../_auth.js";

const POST_KEY_PATTERN = /^[a-f0-9]{32}$/;
const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9._:-]{16,100}$/;
const PREFERENCE_DOCUMENT_RULE_KEY = "owner-preferences-document-v1";
const RATING_LEVELS = new Set([-2, -1, 1, 2]);
const FEEDBACK_REASON_CODES = new Set([
  "topic",
  "format",
  "esports",
  "promotional",
  "source",
  "other",
]);
const MAX_FEEDBACK_KEYS = 100;
const MAX_HIDDEN_ITEMS = 200;
const MAX_PREFERENCE_DOCUMENT_CHARS = 1000;
const MAX_BODY_BYTES = 4096;

function jsonResponse(body, status = 200, cookies = []) {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    Vary: "Cookie",
  });
  for (const cookie of cookies) {
    headers.append("Set-Cookie", cookie);
  }
  return new Response(JSON.stringify(body), { status, headers });
}

export async function resolveActor(request, env) {
  return resolveAuthIdentity(request, env);
}

function resourceFromRequest(request) {
  const pathname = new URL(request.url).pathname.replace(/\/+$/u, "");
  const prefix = "/api/game-news/";
  if (!pathname.startsWith(prefix)) {
    return "";
  }
  const resource = pathname.slice(prefix.length);
  return /^[a-z-]+$/u.test(resource) ? resource : "";
}

function resultRows(result) {
  return Array.isArray(result?.results) ? result.results : [];
}

async function queryAll(db, sql, bindings = []) {
  return resultRows(await db.prepare(sql).bind(...bindings).all());
}

function normalizePostKey(value) {
  const postKey = String(value ?? "").trim().toLowerCase();
  return POST_KEY_PATTERN.test(postKey) ? postKey : null;
}

function normalizeIdempotencyKey(value) {
  const key = String(value ?? "").trim();
  return IDEMPOTENCY_KEY_PATTERN.test(key) ? key : null;
}

function normalizePreferenceDocument(value) {
  if (typeof value !== "string") {
    return null;
  }
  const content = value.replace(/\r\n?/gu, "\n").trim();
  return content.length <= MAX_PREFERENCE_DOCUMENT_CHARS ? content : null;
}

function normalizeReasonCode(value) {
  if (value === null || value === undefined || value === "") {
    return null;
  }
  const reasonCode = String(value).trim();
  return FEEDBACK_REASON_CODES.has(reasonCode) ? reasonCode : undefined;
}

function requestIsSameOriginWrite(request) {
  const url = new URL(request.url);
  const origin = request.headers.get("Origin");
  const fetchSite = request.headers.get("Sec-Fetch-Site");
  return (
    (!origin || origin === url.origin) &&
    (!fetchSite || fetchSite === "same-origin" || fetchSite === "none") &&
    request.headers.get("X-TodayCommunity-Write") === "1"
  );
}

async function readJsonBody(request) {
  if (!String(request.headers.get("Content-Type") || "")
    .toLowerCase()
    .startsWith("application/json")) {
    throw new RequestError(415, "JSON 요청만 지원합니다.");
  }
  const contentLength = Number(request.headers.get("Content-Length") || 0);
  if (Number.isFinite(contentLength) && contentLength > MAX_BODY_BYTES) {
    throw new RequestError(413, "요청이 너무 큽니다.");
  }
  const raw = await request.text();
  if (new TextEncoder().encode(raw).length > MAX_BODY_BYTES) {
    throw new RequestError(413, "요청이 너무 큽니다.");
  }
  let body;
  try {
    body = JSON.parse(raw);
  } catch {
    throw new RequestError(400, "JSON 형식이 올바르지 않습니다.");
  }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    throw new RequestError(400, "요청 본문은 객체여야 합니다.");
  }
  return body;
}

class RequestError extends Error {
  constructor(status, message, cookies = []) {
    super(message);
    this.status = status;
    this.cookies = cookies;
  }
}

function requireCapability(identity, capability) {
  if (!identity.capabilities?.[capability]) {
    throw new RequestError(
      401,
      "시크릿 링크 인증이 필요한 기능입니다.",
      identity.cookies_to_clear
    );
  }
}

function withIdentityCookies(response, identity) {
  const cookies = identity?.cookies_to_clear || [];
  if (!cookies.length) {
    return response;
  }
  const headers = new Headers(response.headers);
  for (const cookie of cookies) {
    headers.append("Set-Cookie", cookie);
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function findPost(db, postKey) {
  const rows = await queryAll(
    db,
    `
    SELECT
      p.id AS post_id,
      p.external_post_id AS post_key,
      p.status,
      p.title,
      p.post_url,
      p.subject,
      p.last_seen_at,
      c.id AS candidate_id,
      c.current_evaluation_id AS evaluation_id
    FROM posts AS p
    JOIN game_news_candidates AS c
      ON p.canonical_post_key = 'game-news:' || c.url_sha256
    WHERE p.archive_key = 'game-news'
      AND p.external_post_id = ?
    LIMIT 2
    `,
    [postKey]
  );
  if (rows.length !== 1) {
    throw new RequestError(404, "게임 뉴스 글을 찾지 못했습니다.");
  }
  return rows[0];
}

async function listFeedbackState(db, actor, postKeys) {
  if (!postKeys.length) {
    return [];
  }
  const placeholders = postKeys.map(() => "?").join(", ");
  const rows = await queryAll(
    db,
    `
    SELECT
      p.external_post_id AS post_key,
      CASE
        WHEN f.feedback_type = 'clear' THEN NULL
        ELSE coalesce(
          f.rating_level,
          CASE f.feedback_type WHEN 'like' THEN 1 WHEN 'dislike' THEN -1 END
        )
      END AS rating_level,
      f.id AS feedback_version,
      f.reason_code,
      CASE WHEN p.status = 'hidden' THEN 1 ELSE 0 END AS hidden
    FROM posts AS p
    JOIN game_news_candidates AS c
      ON p.canonical_post_key = 'game-news:' || c.url_sha256
    LEFT JOIN game_news_feedback AS f
      ON f.id = (
        SELECT MAX(latest.id)
        FROM game_news_feedback AS latest
        WHERE latest.candidate_id = c.id
          AND latest.actor = ?
      )
    WHERE p.archive_key = 'game-news'
      AND p.external_post_id IN (${placeholders})
    ORDER BY p.external_post_id
    `,
    [actor, ...postKeys]
  );
  const byKey = new Map(rows.map((row) => [String(row.post_key), row]));
  return postKeys.map((postKey) => {
    const row = byKey.get(postKey);
    return {
      post_key: postKey,
      rating_level: row?.rating_level !== null
        && row?.rating_level !== undefined
        && Number.isInteger(Number(row.rating_level))
        ? Number(row.rating_level)
        : null,
      feedback_version: Number(row?.feedback_version || 0),
      reason_code: row?.reason_code || null,
      hidden: Number(row?.hidden || 0) === 1,
    };
  });
}

async function getFeedback(request, db, actor) {
  const url = new URL(request.url);
  const values = url.searchParams.getAll("post_key");
  if (values.length > MAX_FEEDBACK_KEYS) {
    throw new RequestError(400, "한 번에 조회할 수 있는 글 수를 초과했습니다.");
  }
  const postKeys = [];
  const seen = new Set();
  for (const value of values) {
    const postKey = normalizePostKey(value);
    if (!postKey) {
      throw new RequestError(400, "글 식별자가 올바르지 않습니다.");
    }
    if (!seen.has(postKey)) {
      seen.add(postKey);
      postKeys.push(postKey);
    }
  }
  return jsonResponse({ items: await listFeedbackState(db, actor, postKeys) });
}

async function postFeedback(request, db, actor) {
  const body = await readJsonBody(request);
  const postKey = normalizePostKey(body.post_key);
  const idempotencyKey = normalizeIdempotencyKey(body.idempotency_key);
  const ratingLevel = body.rating_level;
  const reasonCode = normalizeReasonCode(body.reason_code);
  if (!postKey || !idempotencyKey) {
    throw new RequestError(400, "글 또는 요청 식별자가 올바르지 않습니다.");
  }
  if (ratingLevel !== null && !RATING_LEVELS.has(ratingLevel)) {
    throw new RequestError(400, "평가 단계가 올바르지 않습니다.");
  }
  if (reasonCode === undefined) {
    throw new RequestError(400, "평가 이유가 올바르지 않습니다.");
  }
  const post = await findPost(db, postKey);
  const feedbackType = ratingLevel === null
    ? "clear"
    : ratingLevel > 0 ? "like" : "dislike";
  const values = [
    Number(post.candidate_id),
    post.evaluation_id === null ? null : Number(post.evaluation_id),
    feedbackType,
    ratingLevel,
    reasonCode,
    null,
    actor,
    idempotencyKey,
    new Date().toISOString(),
  ];
  const existing = await queryAll(
    db,
    "SELECT * FROM game_news_feedback WHERE idempotency_key = ?",
    [idempotencyKey]
  );
  if (existing.length) {
    assertIdempotentMatch(existing[0], [
      "candidate_id",
      "evaluation_id",
      "feedback_type",
      "rating_level",
      "reason_code",
      "note",
      "actor",
    ], values.slice(0, 7));
  } else {
    const inserted = await queryAll(
      db,
      `
      INSERT INTO game_news_feedback (
        candidate_id, evaluation_id, feedback_type, rating_level,
        reason_code, note, actor, idempotency_key, created_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(idempotency_key) DO NOTHING
      RETURNING id
      `,
      values
    );
    if (!inserted.length) {
      const raced = await queryAll(
        db,
        "SELECT * FROM game_news_feedback WHERE idempotency_key = ?",
        [idempotencyKey]
      );
      if (raced.length !== 1) {
        throw new Error("Feedback idempotency lookup failed");
      }
      assertIdempotentMatch(raced[0], [
        "candidate_id",
        "evaluation_id",
        "feedback_type",
        "rating_level",
        "reason_code",
        "note",
        "actor",
      ], values.slice(0, 7));
    }
  }
  const [state] = await listFeedbackState(db, actor, [postKey]);
  return jsonResponse({ item: state }, 201);
}

function assertIdempotentMatch(row, columns, values) {
  const matches = columns.every((column, index) => {
    const actual = row[column] ?? null;
    const expected = values[index] ?? null;
    return String(actual) === String(expected);
  });
  if (!matches) {
    throw new RequestError(409, "같은 요청 식별자가 다른 내용에 사용되었습니다.");
  }
}

async function getHidden(db) {
  const rows = await queryAll(
    db,
    `
    SELECT external_post_id AS post_key, title, post_url, subject, last_seen_at
    FROM posts
    WHERE archive_key = 'game-news' AND status = 'hidden'
    ORDER BY last_seen_at DESC, id DESC
    LIMIT ${MAX_HIDDEN_ITEMS}
    `
  );
  return jsonResponse({ items: rows });
}

async function postVisibility(request, db, actor) {
  const body = await readJsonBody(request);
  const postKey = normalizePostKey(body.post_key);
  const idempotencyKey = normalizeIdempotencyKey(body.idempotency_key);
  const action = body.action;
  if (!postKey || !idempotencyKey || !["hide", "restore"].includes(action)) {
    throw new RequestError(400, "숨김 요청이 올바르지 않습니다.");
  }
  const post = await findPost(db, postKey);
  const values = [
    Number(post.candidate_id),
    post.evaluation_id === null ? null : Number(post.evaluation_id),
    action,
    actor,
    idempotencyKey,
    new Date().toISOString(),
  ];
  const existing = await queryAll(
    db,
    "SELECT * FROM game_news_visibility_events WHERE idempotency_key = ?",
    [idempotencyKey]
  );
  if (existing.length) {
    assertIdempotentMatch(existing[0], [
      "candidate_id",
      "evaluation_id",
      "action",
      "actor",
    ], values.slice(0, 4));
  } else {
    const inserted = await queryAll(
      db,
      `
      INSERT INTO game_news_visibility_events (
        candidate_id, evaluation_id, action, actor, idempotency_key,
        created_at
      ) VALUES (?, ?, ?, ?, ?, ?)
      ON CONFLICT(idempotency_key) DO NOTHING
      RETURNING id
      `,
      values
    );
    if (!inserted.length) {
      const raced = await queryAll(
        db,
        "SELECT * FROM game_news_visibility_events WHERE idempotency_key = ?",
        [idempotencyKey]
      );
      if (raced.length !== 1) {
        throw new Error("Visibility idempotency lookup failed");
      }
      assertIdempotentMatch(raced[0], [
        "candidate_id",
        "evaluation_id",
        "action",
        "actor",
      ], values.slice(0, 4));
    }
  }
  await db.prepare(
    "UPDATE posts SET status = ? WHERE id = ? AND archive_key = 'game-news'"
  ).bind(action === "hide" ? "hidden" : "active", Number(post.post_id)).run();
  return jsonResponse({
    item: {
      post_key: postKey,
      hidden: action === "hide",
    },
  }, 201);
}

function preferenceDocumentFromRows(rows) {
  const row = rows[0];
  if (!row) {
    return {
      content: "",
      version: 0,
      updated_at: null,
      max_length: MAX_PREFERENCE_DOCUMENT_CHARS,
    };
  }
  return {
    content: row.action === "set" ? String(row.rule_text || "") : "",
    version: Number(row.rule_event_id),
    updated_at: row.created_at || null,
    max_length: MAX_PREFERENCE_DOCUMENT_CHARS,
  };
}

async function loadPreferenceDocument(db) {
  const rows = await queryAll(
    db,
    `
    SELECT
      r.id AS rule_event_id,
      r.action,
      r.rule_text,
      r.created_at
    FROM game_news_manual_rule_events AS r
    WHERE r.rule_key = ?
    ORDER BY r.id DESC
    LIMIT 1
    `,
    [PREFERENCE_DOCUMENT_RULE_KEY]
  );
  return preferenceDocumentFromRows(rows);
}

async function getPreferenceDocument(db) {
  return jsonResponse({ document: await loadPreferenceDocument(db) });
}

async function postPreferenceDocument(request, db, actor) {
  const body = await readJsonBody(request);
  const idempotencyKey = normalizeIdempotencyKey(body.idempotency_key);
  const content = normalizePreferenceDocument(body.content);
  const baseVersion = body.base_version;
  if (
    content === null
    || !idempotencyKey
    || !Number.isSafeInteger(baseVersion)
    || baseVersion < 0
  ) {
    throw new RequestError(400, "선호 전문 요청이 올바르지 않습니다.");
  }
  const action = content ? "set" : "retract";
  const values = [
    PREFERENCE_DOCUMENT_RULE_KEY,
    action,
    content || null,
    content ? "strong" : null,
    actor,
    idempotencyKey,
    new Date().toISOString(),
  ];
  const existing = await queryAll(
    db,
    "SELECT * FROM game_news_manual_rule_events WHERE idempotency_key = ?",
    [idempotencyKey]
  );
  if (existing.length) {
    assertIdempotentMatch(existing[0], [
      "rule_key",
      "action",
      "rule_text",
      "strength",
      "actor",
    ], values.slice(0, 5));
  } else {
    const inserted = await queryAll(
      db,
      `
      INSERT INTO game_news_manual_rule_events (
        rule_key, action, rule_text, strength, actor, idempotency_key,
        created_at
      )
      SELECT ?, ?, ?, ?, ?, ?, ?
      WHERE COALESCE((
        SELECT MAX(id)
        FROM game_news_manual_rule_events
        WHERE rule_key = ?
      ), 0) = ?
      ON CONFLICT(idempotency_key) DO NOTHING
      RETURNING id
      `,
      [...values, PREFERENCE_DOCUMENT_RULE_KEY, baseVersion]
    );
    if (!inserted.length) {
      const raced = await queryAll(
        db,
        "SELECT * FROM game_news_manual_rule_events WHERE idempotency_key = ?",
        [idempotencyKey]
      );
      if (!raced.length) {
        throw new RequestError(
          409,
          "다른 기기에서 선호 전문이 변경되었습니다. 최신 내용을 다시 불러와 주세요."
        );
      }
      if (raced.length !== 1) {
        throw new Error("Preference document idempotency lookup failed");
      }
      assertIdempotentMatch(raced[0], [
        "rule_key",
        "action",
        "rule_text",
        "strength",
        "actor",
      ], values.slice(0, 5));
    }
  }
  return jsonResponse({ document: await loadPreferenceDocument(db) }, 201);
}

export async function onRequestGet(context) {
  try {
    const resource = resourceFromRequest(context.request);
    const db = context.env.DB;
    const identity = await resolveActor(context.request, context.env);
    if (resource === "session") {
      return jsonResponse({
        actor: identity.actor,
        capabilities: identity.capabilities,
        authentication: identity.state,
      }, 200, identity.cookies_to_clear);
    }
    let response;
    if (resource === "feedback") {
      requireCapability(identity, "rate");
      if (!db) {
        throw new Error("D1 binding is unavailable");
      }
      response = await getFeedback(context.request, db, identity.actor);
    } else if (resource === "hidden") {
      requireCapability(identity, "hide");
      if (!db) {
        throw new Error("D1 binding is unavailable");
      }
      response = await getHidden(db);
    } else if (resource === "preferences") {
      requireCapability(identity, "manage_rules");
      if (!db) {
        throw new Error("D1 binding is unavailable");
      }
      response = await getPreferenceDocument(db);
    } else {
      return jsonResponse({ error: "지원하지 않는 경로입니다." }, 404);
    }
    return withIdentityCookies(response, identity);
  } catch (error) {
    if (error instanceof RequestError) {
      return jsonResponse({ error: error.message }, error.status, error.cookies);
    }
    if (error instanceof AuthConfigurationError) {
      console.error("Authentication configuration failed", error.message);
      return jsonResponse({ error: "인증 서비스를 사용할 수 없습니다." }, 503);
    }
    console.error("Game-news owner API failed", error);
    return jsonResponse({ error: "요청을 처리하지 못했습니다." }, 500);
  }
}

export async function onRequestPost(context) {
  try {
    if (!requestIsSameOriginWrite(context.request)) {
      throw new RequestError(403, "허용되지 않은 쓰기 요청입니다.");
    }
    const resource = resourceFromRequest(context.request);
    const identity = await resolveActor(context.request, context.env);
    const db = context.env.DB;
    let response;
    if (resource === "feedback") {
      requireCapability(identity, "rate");
      if (!db) {
        throw new Error("D1 binding is unavailable");
      }
      response = await postFeedback(context.request, db, identity.actor);
    } else if (resource === "visibility") {
      requireCapability(identity, "hide");
      if (!db) {
        throw new Error("D1 binding is unavailable");
      }
      response = await postVisibility(context.request, db, identity.actor);
    } else if (resource === "preferences") {
      requireCapability(identity, "manage_rules");
      if (!db) {
        throw new Error("D1 binding is unavailable");
      }
      response = await postPreferenceDocument(context.request, db, identity.actor);
    } else {
      return jsonResponse({ error: "지원하지 않는 경로입니다." }, 404);
    }
    return withIdentityCookies(response, identity);
  } catch (error) {
    if (error instanceof RequestError) {
      return jsonResponse({ error: error.message }, error.status, error.cookies);
    }
    if (error instanceof AuthConfigurationError) {
      console.error("Authentication configuration failed", error.message);
      return jsonResponse({ error: "인증 서비스를 사용할 수 없습니다." }, 503);
    }
    console.error("Game-news owner API failed", error);
    return jsonResponse({ error: "요청을 처리하지 못했습니다." }, 500);
  }
}
