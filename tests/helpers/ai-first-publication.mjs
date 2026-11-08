import { DatabaseSync } from "node:sqlite";

// Only replace SQLite's clock input. Native formatting and the production
// include-history, normalization, insertion and immutability triggers still run.
export function publicationClock(sqlite) {
  const native = new DatabaseSync(":memory:");
  let now;
  sqlite.function("strftime", { varargs: true }, (...args) => native
    .prepare(`SELECT strftime(${args.map(() => "?").join(",")}) AS value`)
    .get(...args.map(value => value === "now" && now ? now : value)).value);
  return { set(value) { now = value; }, close() { native.close(); } };
}

// Seed the real include ledger before creating an AI post. The production
// first-publication guards remain enabled in public archive SQL tests.
export function seedAiInclude(sqlite, { itemId, evaluatedAt, title = "fixture", sourceKey = "ai-practice-hermes" }) {
  const url = `https://example.com/ai/${itemId}`;
  sqlite.prepare(`INSERT INTO ai_practice_candidates
    (item_id,source_key,canonical_url,content_hash,instruction_version,case_key,original_title,title,
     summary,subject,evidence_url,evidence_level,content_kind,published_at_precision,first_seen_at,last_seen_at,decision)
    VALUES (?,?,?,'fixture-hash','ai-practice-v10',?,?,?,'fixture summary','프로젝트 운영',?,
      'full_text','experience','second',?,?,'include')`).run(
    itemId, sourceKey, url, itemId, title, title, url, evaluatedAt, evaluatedAt);
  sqlite.prepare(`INSERT INTO ai_practice_evaluations
    (item_id,run_id,content_hash,decision,reason,evidence_quote,collection_model,filter_model,instruction_version,evaluated_at)
    VALUES (?,'fixture-run','fixture-hash','include','fixture include','fixture quote',
      'gpt-5.6-sol','gpt-5.6-terra','ai-practice-v10',?)`).run(itemId, evaluatedAt);
  return `ai-practice:${itemId}`;
}
