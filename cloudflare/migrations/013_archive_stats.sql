CREATE TABLE IF NOT EXISTS archive_stats (
  archive_key TEXT PRIMARY KEY,
  active_post_count INTEGER NOT NULL DEFAULT 0
    CHECK (active_post_count >= 0),
  latest_seen_at TEXT NOT NULL DEFAULT ''
    CHECK (length(latest_seen_at) <= 64),
  subject_options_json TEXT NOT NULL DEFAULT '[]' CHECK (
    length(subject_options_json) >= 2
    AND json_valid(subject_options_json)
    AND json_type(subject_options_json) = 'array'
  ),
  stats_version INTEGER NOT NULL DEFAULT 0 CHECK (stats_version >= 0),
  mutation_token TEXT NOT NULL DEFAULT ''
    CHECK (length(mutation_token) <= 64),
  updated_at TEXT NOT NULL
    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  FOREIGN KEY (archive_key) REFERENCES archives(archive_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS archive_subject_stats (
  archive_key TEXT NOT NULL,
  subject TEXT NOT NULL CHECK (length(trim(subject)) > 0),
  active_post_count INTEGER NOT NULL DEFAULT 0
    CHECK (active_post_count >= 0),
  updated_at TEXT NOT NULL
    DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  PRIMARY KEY (archive_key, subject),
  FOREIGN KEY (archive_key) REFERENCES archives(archive_key) ON DELETE CASCADE
);

-- Build only the prefix-free indexes in this quota window. Existing
-- archive-prefixed indexes stay usable until 014 upgrades them separately.
CREATE INDEX IF NOT EXISTS idx_posts_active_created
  ON posts (created_at DESC, id DESC)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_posts_active_upvotes
  ON posts (upvotes DESC, created_at DESC, id DESC)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_posts_active_comments
  ON posts (comments DESC, created_at DESC, id DESC)
  WHERE status = 'active';

INSERT INTO archive_stats (
  archive_key, active_post_count, latest_seen_at, subject_options_json,
  stats_version, updated_at
)
SELECT
  a.archive_key,
  coalesce(p.active_post_count, 0),
  coalesce(p.latest_seen_at, ''),
  '[]',
  1,
  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
FROM archives AS a
LEFT JOIN (
  SELECT
    archive_key,
    count(*) AS active_post_count,
    max(last_seen_at) AS latest_seen_at
  FROM posts
  WHERE status = 'active'
  GROUP BY archive_key
) AS p ON p.archive_key = a.archive_key
WHERE true
ON CONFLICT(archive_key) DO UPDATE SET
  active_post_count = excluded.active_post_count,
  latest_seen_at = excluded.latest_seen_at,
  stats_version = max(archive_stats.stats_version, 1),
  updated_at = excluded.updated_at;

DELETE FROM archive_subject_stats;

INSERT INTO archive_subject_stats (
  archive_key, subject, active_post_count, updated_at
)
SELECT
  archive_key,
  trim(subject),
  count(*),
  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
FROM posts
WHERE status = 'active'
  AND trim(subject) <> ''
GROUP BY archive_key, trim(subject);

UPDATE archive_stats
SET subject_options_json = coalesce((
  SELECT json_group_array(subject)
  FROM (
    SELECT subject
    FROM archive_subject_stats
    WHERE archive_key = archive_stats.archive_key
      AND active_post_count > 0
      AND length(subject) <= 100
    ORDER BY subject COLLATE NOCASE ASC, subject ASC
    LIMIT 100
  )
), '[]');
