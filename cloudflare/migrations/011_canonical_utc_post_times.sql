-- Persist every machine-readable timestamp in one fixed-width UTC RFC 3339
-- form. Source text remains untouched in posts.created_at_raw.
ALTER TABLE posts ADD COLUMN created_at_basis TEXT NOT NULL DEFAULT 'source'
  CHECK (created_at_basis IN ('source', 'first_seen'));

ALTER TABLE posts ADD COLUMN created_at_precision TEXT NOT NULL DEFAULT 'second'
  CHECK (created_at_precision IN ('second', 'minute', 'date'));

-- A projected game-news row uses the publisher time when it was available at
-- projection time. When the effective value equals a later-discovered
-- publisher time, prefer that stronger provenance; otherwise an exact match
-- with first_seen_at is the collection-time fallback.
UPDATE posts
SET created_at_basis = CASE
      WHEN archive_key <> 'game-news' THEN 'source'
      WHEN EXISTS (
        SELECT 1
        FROM game_news_candidates AS candidate
        WHERE posts.canonical_post_key = 'game-news:' || candidate.url_sha256
          AND candidate.published_at IS NOT NULL
          AND unixepoch(candidate.published_at) = unixepoch(posts.created_at)
      ) THEN 'source'
      WHEN unixepoch(created_at) = unixepoch(first_seen_at) THEN 'first_seen'
      ELSE 'source'
    END,
    created_at_precision = CASE
      WHEN archive_key = 'game-news'
        AND NOT EXISTS (
          SELECT 1
          FROM game_news_candidates AS candidate
          WHERE posts.canonical_post_key = 'game-news:' || candidate.url_sha256
            AND candidate.published_at IS NOT NULL
            AND unixepoch(candidate.published_at) = unixepoch(posts.created_at)
        )
        AND unixepoch(created_at) = unixepoch(first_seen_at)
      THEN 'second'
      WHEN archive_key = 'game-news' THEN CASE
        WHEN (
            created_at_raw GLOB '*[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*'
            OR created_at_raw GLOB '*[0-9][0-9][0-9][0-9].[0-9][0-9].[0-9][0-9]*'
            OR created_at_raw GLOB '*[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]*'
            OR (
              instr(created_at_raw, '년') > 0
              AND instr(created_at_raw, '월') > 0
              AND instr(created_at_raw, '일') > 0
            )
          )
          AND instr(created_at_raw, ':') = 0
          AND instr(created_at_raw, '시') = 0
          AND instr(created_at_raw, '분') = 0
          AND instr(created_at_raw, '초') = 0
        THEN 'date'
        WHEN instr(created_at_raw, '초') > 0
          OR length(created_at_raw) - length(replace(created_at_raw, ':', '')) >= 2
        THEN 'second'
        WHEN instr(created_at_raw, ':') > 0
          OR instr(created_at_raw, '시') > 0
          OR instr(created_at_raw, '분') > 0
        THEN 'minute'
        ELSE 'second'
      END
      WHEN source_key LIKE 'dcinside-%'
        AND strftime('%H:%M:%S', created_at, '+9 hours') = '23:59:59'
        AND instr(created_at_raw, ':') = 0
      THEN 'date'
      WHEN source_key LIKE 'dcinside-%' THEN 'second'
      WHEN source_key LIKE 'fmkorea-%' THEN CASE
        WHEN strftime('%H:%M:%S', created_at, '+9 hours') = '23:59:59'
          AND instr(created_at_raw, ':') = 0
        THEN 'date'
        WHEN instr(created_at_raw, '초') > 0
          OR length(created_at_raw) - length(replace(created_at_raw, ':', '')) >= 2
        THEN 'second'
        WHEN instr(created_at_raw, '분') > 0
          OR instr(created_at_raw, '시간') > 0
          OR instr(created_at_raw, '일 전') > 0
          OR instr(created_at_raw, ':') > 0
        THEN 'minute'
        WHEN instr(created_at_raw, ':') = 0 THEN 'date'
        ELSE 'second'
      END
      WHEN length(created_at_raw) - length(replace(created_at_raw, ':', '')) >= 2
      THEN 'second'
      WHEN instr(created_at_raw, ':') > 0 THEN 'minute'
      ELSE 'second'
    END,
    created_at = strftime('%Y-%m-%dT%H:%M:%SZ', created_at),
    fetched_at = strftime('%Y-%m-%dT%H:%M:%SZ', fetched_at),
    first_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ', first_seen_at),
    last_seen_at = strftime('%Y-%m-%dT%H:%M:%SZ', last_seen_at);

UPDATE archives
SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', created_at),
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', updated_at);

UPDATE sources
SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', created_at),
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', updated_at);

UPDATE crawl_runs
SET started_at = strftime('%Y-%m-%dT%H:%M:%SZ', started_at),
    finished_at = CASE
      WHEN finished_at IS NULL OR trim(finished_at) = '' THEN finished_at
      ELSE strftime('%Y-%m-%dT%H:%M:%SZ', finished_at)
    END;

UPDATE source_state
SET backfill_anchor_created_at = CASE
      WHEN backfill_anchor_created_at IS NULL
        OR trim(backfill_anchor_created_at) = ''
      THEN backfill_anchor_created_at
      ELSE strftime('%Y-%m-%dT%H:%M:%SZ', backfill_anchor_created_at)
    END,
    blocked_until = CASE
      WHEN blocked_until IS NULL OR trim(blocked_until) = '' THEN blocked_until
      ELSE strftime('%Y-%m-%dT%H:%M:%SZ', blocked_until)
    END,
    last_blocked_at = CASE
      WHEN last_blocked_at IS NULL OR trim(last_blocked_at) = ''
      THEN last_blocked_at
      ELSE strftime('%Y-%m-%dT%H:%M:%SZ', last_blocked_at)
    END,
    created_at = strftime('%Y-%m-%dT%H:%M:%SZ', created_at),
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', updated_at);

UPDATE coverage_intervals
SET oldest_created_at = CASE
      WHEN trim(oldest_created_at) = '' THEN oldest_created_at
      ELSE strftime('%Y-%m-%dT%H:%M:%SZ', oldest_created_at)
    END,
    newest_created_at = CASE
      WHEN trim(newest_created_at) = '' THEN newest_created_at
      ELSE strftime('%Y-%m-%dT%H:%M:%SZ', newest_created_at)
    END,
    checked_at = strftime('%Y-%m-%dT%H:%M:%SZ', checked_at),
    created_at = strftime('%Y-%m-%dT%H:%M:%SZ', created_at),
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', updated_at);

UPDATE coverage_absences
SET checked_at = strftime('%Y-%m-%dT%H:%M:%SZ', checked_at),
    created_at = strftime('%Y-%m-%dT%H:%M:%SZ', created_at),
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', updated_at);
