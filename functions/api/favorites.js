import { AuthConfigurationError, resolveAuthIdentity } from "./_auth.js";

const MAX_SCAN = 512;
const PAGE_SIZE = 30;
const visible = "p.status = 'active' AND a.is_public = 1";

function response(body, status = 200, cookies = []) {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    Vary: "Cookie",
  });
  for (const cookie of cookies) headers.append("Set-Cookie", cookie);
  return new Response(JSON.stringify(body), { status, headers });
}

class RequestError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function positiveId(value) {
  return /^\d{1,16}$/u.test(String(value)) && Number.isSafeInteger(Number(value))
    && Number(value) > 0 ? Number(value) : null;
}

async function rows(db, sql, values = []) {
  return (await db.prepare(sql).bind(...values).all()).results || [];
}

async function list(db, params) {
  const rawCursor = params.get("cursor") || "";
  const match = /^(next|previous):(\d{1,16}):(\d{1,7})$/u.exec(rawCursor);
  if (rawCursor && (!match || !positiveId(match[2]) || !positiveId(match[3]))) {
    throw new RequestError(400, "즐겨찾기 페이지 주소가 올바르지 않습니다.");
  }
  const previous = match?.[1] === "previous";
  const page = match ? Number(match[3]) : 1;
  const boundary = match ? Number(match[2]) : null;
  const order = previous ? "ASC" : "DESC";
  // Bound candidate reads even when many saved posts have become hidden/private.
  const candidates = await rows(db, `
    WITH candidates AS MATERIALIZED (
      SELECT id, post_id, saved_at FROM shared_favorites
      ${boundary ? `WHERE id ${previous ? ">" : "<"} ?` : ""}
      ORDER BY id ${order} LIMIT ${MAX_SCAN}
    )
    SELECT f.id AS favorite_id, f.saved_at, p.id AS post_id,
      p.archive_key, p.source_key, p.external_post_id, p.title, p.post_url,
      p.subject, p.created_at, p.upvotes, p.comments, p.last_seen_at,
      CASE WHEN ${visible} THEN 1 ELSE 0 END AS visible
    FROM candidates f
    LEFT JOIN posts p ON p.id = f.post_id
    LEFT JOIN archives a ON a.archive_key = p.archive_key
    ORDER BY f.id ${order}
  `, boundary ? [boundary] : []);
  const matches = candidates.filter(item => item.visible === 1);
  const selected = matches.slice(0, PAGE_SIZE);
  const more = matches.length > PAGE_SIZE || candidates.length === MAX_SCAN;
  const scanned = matches.length > PAGE_SIZE ? selected.at(-1) : candidates.at(-1);
  const first = candidates[0]?.favorite_id ?? boundary;
  const last = scanned?.favorite_id ?? boundary;
  const hasPrevious = previous ? page > 1 && more : page > 1;
  const hasNext = previous ? true : more;
  const counts = await rows(db, `SELECT count(*) AS total_posts, max(f.saved_at) AS latest_seen_at
    FROM shared_favorites f CROSS JOIN posts p ON p.id = f.post_id
    CROSS JOIN archives a ON a.archive_key = p.archive_key WHERE ${visible}`);
  const catalog = await rows(db, `SELECT archive_key, display_name, description, content_kind, display_order
    FROM archives WHERE is_public = 1 ORDER BY display_order, archive_key`);
  return {
    target: "favorites", archives: [...catalog, { archive_key: "all", display_name: "모두", content_kind: "mixed", display_order: 100 }],
    summary: counts[0], sources: [], runs: [],
    posts: (previous ? selected.reverse() : selected).map(({ visible: _visible, ...post }) => post),
    pagination: {
      mode: "sequential", bounded_scan: true, page, page_size: PAGE_SIZE, quick_page_count: 0,
      has_previous: hasPrevious, has_next: hasNext,
      previous_cursor: hasPrevious && (previous ? last : first) ? `previous:${previous ? last : first}:${page - 1}` : "",
      next_cursor: hasNext && (previous ? first : last) ? `next:${previous ? first : last}:${page + 1}` : "",
    },
  };
}

export async function onRequest(context) {
  let identity;
  try {
    identity = await resolveAuthIdentity(context.request, context.env);
    // Match the existing secret-link-only archive preferences, including revocation checks.
    if (identity.state !== "authenticated") {
      throw new RequestError(401, "시크릿 링크 인증이 필요한 기능입니다.");
    }
    const { request, env: { DB: db } } = context;
    const params = new URL(request.url).searchParams;
    let result;
    if (request.method === "GET") {
      if (params.has("ids")) {
        const ids = params.get("ids").split(",").map(positiveId);
        if (!ids.length || ids.length > 100 || ids.includes(null)) {
          throw new RequestError(400, "글 번호가 올바르지 않습니다.");
        }
        const items = await rows(db, `SELECT f.post_id FROM shared_favorites f
          JOIN posts p ON p.id = f.post_id JOIN archives a ON a.archive_key = p.archive_key
          WHERE f.post_id IN (${ids.map(() => "?").join(",")}) AND ${visible}`, ids);
        result = { post_ids: items.map(item => item.post_id) };
      } else {
        result = await list(db, params);
      }
    } else if (request.method === "POST") {
      const origin = request.headers.get("Origin");
      const site = request.headers.get("Sec-Fetch-Site");
      if ((origin && origin !== new URL(request.url).origin)
        || (site && !["same-origin", "none"].includes(site))
        || request.headers.get("X-TodayCommunity-Write") !== "1") {
        throw new RequestError(403, "같은 사이트에서만 변경할 수 있습니다.");
      }
      if (!/^application\/json(?:;|$)/iu.test(request.headers.get("Content-Type") || "")) {
        throw new RequestError(415, "JSON 요청만 지원합니다.");
      }
      if (Number(request.headers.get("Content-Length")) > 1024) throw new RequestError(413, "요청이 너무 큽니다.");
      const raw = await request.text();
      if (new TextEncoder().encode(raw).length > 1024) throw new RequestError(413, "요청이 너무 큽니다.");
      let body;
      try { body = JSON.parse(raw); } catch { throw new RequestError(400, "JSON 형식이 올바르지 않습니다."); }
      const id = positiveId(body?.post_id);
      if (!id || typeof body?.saved !== "boolean") throw new RequestError(400, "글 번호와 저장 상태가 필요합니다.");
      if (body.saved) {
        const found = await rows(db, `SELECT p.id FROM posts p JOIN archives a ON a.archive_key = p.archive_key
          WHERE p.id = ? AND ${visible}`, [id]);
        if (!found.length) throw new RequestError(404, "저장할 글을 찾지 못했습니다.");
        // Explicit desired state makes retries idempotent; duplicate saves preserve ordering.
        await db.prepare(`INSERT INTO shared_favorites (post_id)
          SELECT p.id FROM posts p JOIN archives a ON a.archive_key = p.archive_key
          WHERE p.id = ? AND ${visible}
          AND NOT EXISTS (SELECT 1 FROM shared_favorites WHERE post_id = p.id)
          ON CONFLICT(post_id) DO NOTHING`).bind(id).run();
      } else {
        await db.prepare("DELETE FROM shared_favorites WHERE post_id = ?").bind(id).run();
      }
      const current = await rows(db, "SELECT post_id FROM shared_favorites WHERE post_id = ?", [id]);
      result = { post_id: id, saved: current.length > 0 };
    } else {
      throw new RequestError(405, "지원하지 않는 요청입니다.");
    }
    return response(result, 200, identity.cookies_to_clear);
  } catch (error) {
    const status = error instanceof RequestError ? error.status : error instanceof AuthConfigurationError ? 503 : 500;
    return response({ error: status < 500 ? error.message : "즐겨찾기를 처리하지 못했습니다. 잠시 후 다시 시도해 주세요." }, status, identity?.cookies_to_clear);
  }
}
