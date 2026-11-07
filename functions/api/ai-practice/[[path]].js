import { AuthConfigurationError, resolveAuthIdentity } from "../_auth.js";

const MAX_LENGTH = 6000;
const MAX_BODY_BYTES = 48 * 1024;
const INSTRUCTION_VERSION = "ai-practice-v8";
const EVENT_ID = /^[A-Za-z0-9._:-]{16,100}$/;
const normalizeContent = value => typeof value === "string" ? value.replace(/\r\n?/gu, "\n").trim() : null;
const rows = result => Array.isArray(result?.results) ? result.results : [];
const query = async (db, sql, values = []) => rows(await db.prepare(sql).bind(...values).all());

class RequestError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

function json(body, status = 200, cookies = []) {
  const headers = new Headers({ "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow", Vary: "Cookie" });
  for (const cookie of cookies) headers.append("Set-Cookie", cookie);
  return new Response(JSON.stringify(body), { status, headers });
}

function sameOriginWrite(request) {
  const origin = request.headers.get("Origin");
  const site = request.headers.get("Sec-Fetch-Site");
  return (!origin || origin === new URL(request.url).origin)
    && (!site || ["same-origin", "none"].includes(site))
    && request.headers.get("X-TodayCommunity-Write") === "1";
}

async function readBody(request) {
  if (!String(request.headers.get("Content-Type") || "").toLowerCase().startsWith("application/json")) {
    throw new RequestError(415, "JSON 요청만 지원합니다.");
  }
  if (Number(request.headers.get("Content-Length")) > MAX_BODY_BYTES) throw new RequestError(413, "요청이 너무 큽니다.");
  const raw = await request.text();
  if (new TextEncoder().encode(raw).length > MAX_BODY_BYTES) throw new RequestError(413, "요청이 너무 큽니다.");
  let body;
  try { body = JSON.parse(raw); } catch { throw new RequestError(400, "JSON 형식이 올바르지 않습니다."); }
  if (!body || typeof body !== "object" || Array.isArray(body)) throw new RequestError(400, "요청 본문은 객체여야 합니다.");
  return body;
}

async function sha256(value) {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value)));
  return [...bytes].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

async function loadDocument(db) {
  const [row] = await query(db, `SELECT s.policy_revision, s.policy_hash, s.updated_at, p.content
    FROM ai_practice_policy_state s JOIN ai_practice_policy_snapshots p
      ON p.policy_revision = s.policy_revision AND p.policy_hash = s.policy_hash WHERE s.id = 1`);
  if (!row) throw new RequestError(503, "AI 실전 선정기준이 아직 준비되지 않았습니다.");
  if (await sha256(normalizeContent(row.content)) !== row.policy_hash) {
    throw new RequestError(503, "AI 실전 선정기준의 저장 무결성을 확인해야 합니다.");
  }
  return { content: normalizeContent(row.content), revision: Number(row.policy_revision),
    hash: row.policy_hash, updated_at: row.updated_at, max_length: MAX_LENGTH };
}

async function loadApplication(db, document) {
  const result = await query(db, `WITH queue_state AS (
      SELECT q.*, (cache_invalidated = 1 OR processed_policy_revision IS NOT ?
        OR processed_policy_hash IS NOT ? OR processed_instruction_version IS NOT ?
        OR (c.item_id IS NOT NULL AND (c.policy_revision IS NOT ? OR c.policy_hash IS NOT ?
          OR c.instruction_version IS NOT ?))) AS stale,
        (processed_instruction_version IS NOT NULL OR c.item_id IS NOT NULL) AS evaluated
      FROM ai_practice_queue q LEFT JOIN ai_practice_candidates c ON c.item_id = q.item_id)
    SELECT s.source_key, s.board_name AS name,
      COALESCE(q.total_count, 0) AS total_count, COALESCE(q.pending_count, 0) AS pending_count,
      COALESCE(m.manifest_pending_count, 0) AS manifest_pending_count,
      COALESCE(q.stale_count, 0) AS stale_count, COALESCE(q.retry_count, 0) AS retry_count,
      COALESCE(q.reassessment_count, 0) AS reassessment_count,
      COALESCE(q.blocked_count, 0) AS blocked_count, COALESCE(q.oversized_count, 0) AS oversized_count,
      COALESCE(q.review_count, 0) AS review_count,
      c.last_success_at, c.last_attempt_at, c.last_status,
      d.status AS discovery_status, d.complete, d.discovered_count, d.last_discovered_at,
      d.last_error, d.cursor_json, d.scope_json
    FROM sources s
    LEFT JOIN (SELECT source_key, COUNT(*) AS total_count,
      SUM(CASE WHEN status IN ('pending','retry','blocked','oversized') OR stale THEN 1 ELSE 0 END) AS pending_count,
      SUM(CASE WHEN (status IN ('pending','retry','blocked','oversized') OR stale)
        AND (work_kind = 'reassessment' OR (stale AND evaluated)) THEN 1 ELSE 0 END) AS reassessment_count,
      SUM(CASE WHEN stale THEN 1 ELSE 0 END) AS stale_count,
      SUM(CASE WHEN status = 'retry' THEN 1 ELSE 0 END) AS retry_count,
      SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked_count,
      SUM(CASE WHEN status = 'oversized' THEN 1 ELSE 0 END) AS oversized_count,
      SUM(CASE WHEN status = 'review' THEN 1 ELSE 0 END) AS review_count
      FROM queue_state GROUP BY source_key) q ON q.source_key = s.source_key
    LEFT JOIN (SELECT source_key, SUM(MAX(item_count - registration_offset, 0)) AS manifest_pending_count
      FROM ai_practice_discovery_manifests WHERE status = 'pending' GROUP BY source_key) m ON m.source_key = s.source_key
    LEFT JOIN ai_practice_source_state c ON c.source_key = s.source_key
    LEFT JOIN ai_practice_discovery_state d ON d.source_key = s.source_key AND d.lane = 'backfill'
    WHERE s.archive_key = 'ai-practice' ORDER BY s.source_key`,
    [document.revision, document.hash, INSTRUCTION_VERSION, document.revision, document.hash, INSTRUCTION_VERSION]);
  const sources = result.map(row => ({ source_key: row.source_key, name: row.name,
    total_count: Number(row.total_count), pending_count: Number(row.pending_count), stale_count: Number(row.stale_count),
    manifest_pending_count: Number(row.manifest_pending_count),
    reassessment_count: Number(row.reassessment_count),
    failed_count: Number(row.retry_count) + Number(row.blocked_count), oversized_count: Number(row.oversized_count),
    review_count: Number(row.review_count), retry_count: Number(row.retry_count), blocked_count: Number(row.blocked_count),
    last_success_at: row.last_success_at || null, last_attempt_at: row.last_attempt_at || null,
    last_status: row.last_status || "not_started",
    backfill: { status: row.discovery_status || "not_started", complete: Number(row.complete) === 1,
      discovered_count: Number(row.discovered_count || 0), last_discovered_at: row.last_discovered_at || null,
      last_error: row.last_error || null, cursor_json: row.cursor_json || null, scope_json: row.scope_json || "{}" } }));
  const counts = Object.fromEntries(["total_count", "manifest_pending_count", "pending_count", "stale_count", "reassessment_count", "failed_count", "oversized_count", "review_count"]
    .map(key => [key, sources.reduce((sum, source) => sum + source[key], 0)]));
  return { ...counts, sources };
}

async function payload(db) {
  const document = await loadDocument(db);
  return { document, application: await loadApplication(db, document) };
}

async function existingEvent(db, eventId) {
  return (await query(db, "SELECT * FROM ai_practice_manual_rule_events WHERE event_id = ?", [eventId]))[0];
}

function assertEvent(event, payloadHash, actor) {
  if (event.payload_hash !== payloadHash || event.actor !== actor) {
    throw new RequestError(409, "같은 저장 요청 ID에 다른 내용이 사용되었습니다.");
  }
}

async function save(request, db, actor) {
  const body = await readBody(request);
  const content = normalizeContent(body.content);
  const base = body.base_revision;
  const eventId = body.event_id;
  if (!content || content.length > MAX_LENGTH || !Number.isSafeInteger(base) || base < 1
      || typeof eventId !== "string" || !EVENT_ID.test(eventId)) {
    throw new RequestError(400, `선정기준은 1–${MAX_LENGTH}자여야 하며 저장 버전과 요청 ID가 필요합니다.`);
  }
  const hash = await sha256(content);
  const payloadHash = await sha256(JSON.stringify({ content, base_revision: base }));
  let event = await existingEvent(db, eventId);
  let replayed = Boolean(event);
  if (!event) {
    const current = await loadDocument(db);
    if (current.revision !== base) {
      const raced = await existingEvent(db, eventId);
      if (raced) {
        assertEvent(raced, payloadHash, actor);
        return { ...(await payload(db)), save: { event_id: eventId, revision: Number(raced.policy_revision),
          changed: Number(raced.policy_revision) !== Number(raced.base_revision), replayed: true } };
      }
      throw new RequestError(409, "다른 곳에서 선정기준이 변경됐습니다. 편집 내용을 보관한 뒤 최신 저장본을 불러와 주세요.");
    }
    const changed = current.hash !== hash;
    const revision = changed ? base + 1 : base;
    const now = new Date().toISOString();
    const statements = [db.prepare(`UPDATE ai_practice_policy_state
      SET policy_revision = ?, policy_hash = ?, updated_at = ?
      WHERE id = 1 AND policy_revision = ?
        AND NOT EXISTS (SELECT 1 FROM ai_practice_manual_rule_events WHERE event_id = ?)`)
      .bind(revision, hash, changed ? now : current.updated_at, base, eventId)];
    if (changed) {
      statements.push(db.prepare(`INSERT INTO ai_practice_policy_snapshots
        (policy_revision, policy_hash, content, created_at, event_id)
        SELECT ?, ?, ?, ?, ? WHERE changes() = 1`).bind(revision, hash, content, now, eventId));
      statements.push(db.prepare(`INSERT OR IGNORE INTO ai_practice_manual_rule_events
        (event_id, base_revision, policy_revision, content, content_hash, payload_hash, actor, created_at)
        SELECT ?, ?, ?, ?, ?, ?, ?, ? FROM ai_practice_policy_snapshots WHERE event_id = ?`)
        .bind(eventId, base, revision, content, hash, payloadHash, actor, now, eventId));
      // A changed revision makes old evaluations logically pending. Saving
      // remains constant-write work even when discovery found thousands of URLs.
    } else {
      statements.push(db.prepare(`INSERT INTO ai_practice_manual_rule_events
        (event_id, base_revision, policy_revision, content, content_hash, payload_hash, actor, created_at)
        SELECT ?, ?, ?, ?, ?, ?, ?, ? WHERE changes() = 1`)
        .bind(eventId, base, revision, content, hash, payloadHash, actor, now));
    }
    const results = await db.batch(statements);
    const eventResult = results[changed ? 2 : 1];
    replayed = Number(eventResult?.meta?.changes ?? eventResult?.changes ?? 0) === 0;
    event = await existingEvent(db, eventId);
    if (!event) throw new RequestError(409, "저장 중 선정기준 버전이 변경됐습니다. 최신 저장본을 확인해 주세요.");
  }
  assertEvent(event, payloadHash, actor);
  return { ...(await payload(db)), save: { event_id: eventId, revision: Number(event.policy_revision),
    changed: Number(event.policy_revision) !== Number(event.base_revision), replayed } };
}

async function handle(context, write) {
  let identity;
  try {
    const path = new URL(context.request.url).pathname.replace(/\/+$/u, "");
    if (path !== "/api/ai-practice/rules") return json({ error: "지원하지 않는 경로입니다." }, 404);
    if (write && !sameOriginWrite(context.request)) throw new RequestError(403, "허용되지 않은 쓰기 요청입니다.");
    identity = await resolveAuthIdentity(context.request, context.env);
    // Secret-link identities also have manage_rules for legacy game settings.
    // AI policy text and backlog details are restricted to password admins.
    if (identity.state !== "admin" || !identity.capabilities?.manage_rules) throw new RequestError(401, "관리자 로그인이 필요합니다.");
    if (!context.env.DB) throw new Error("D1 binding unavailable");
    const result = write ? await save(context.request, context.env.DB, identity.actor) : await payload(context.env.DB);
    return json(result, write ? 201 : 200, identity.cookies_to_clear);
  } catch (error) {
    if (error instanceof RequestError) return json({ error: error.message }, error.status, identity?.cookies_to_clear);
    if (error instanceof AuthConfigurationError) return json({ error: "인증 서비스를 사용할 수 없습니다." }, 503);
    // Never include submitted policy text or database errors in logs/responses.
    console.error("AI practice admin API failed", error?.name || "Error");
    return json({ error: "AI 실전 관리 요청을 처리하지 못했습니다." }, 500, identity?.cookies_to_clear);
  }
}

export const onRequestGet = context => handle(context, false);
export const onRequestPost = context => handle(context, true);
