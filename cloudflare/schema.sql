CREATE TABLE IF NOT EXISTS archives (
  archive_key TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  display_order INTEGER NOT NULL DEFAULT 0,
  is_public INTEGER NOT NULL DEFAULT 1 CHECK (is_public IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO archives (
  archive_key,
  display_name,
  description,
  display_order,
  is_public
) VALUES
  (
    'dcinside-singularity',
    '특이점이 온다',
    '디시인사이드 특이점이 온다 갤러리 인기글',
    10,
    1
  ),
  (
    'dcinside-agent-stack',
    '에이전트 스택',
    '디시인사이드 에이전트 스택 갤러리 인기글',
    20,
    1
  ),
  (
    'fmkorea-munich',
    '뮌헨',
    '에펨코리아의 뮌헨 관련 인기글',
    30,
    1
  );

CREATE TABLE IF NOT EXISTS sources (
  source_key TEXT PRIMARY KEY,
  archive_key TEXT NOT NULL DEFAULT 'dcinside-singularity',
  site_name TEXT NOT NULL,
  board_name TEXT NOT NULL,
  board_url TEXT NOT NULL,
  min_upvotes INTEGER NOT NULL DEFAULT 0,
  min_comments INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (archive_key) REFERENCES archives(archive_key)
);

CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key TEXT NOT NULL,
  archive_key TEXT NOT NULL DEFAULT 'dcinside-singularity',
  canonical_post_key TEXT,
  external_post_id TEXT NOT NULL,
  post_url TEXT NOT NULL,
  subject TEXT NOT NULL DEFAULT '',
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_at_raw TEXT NOT NULL,
  upvotes INTEGER NOT NULL DEFAULT 0,
  comments INTEGER NOT NULL DEFAULT 0,
  fetched_at TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  qualifies_by TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  UNIQUE(source_key, external_post_id),
  UNIQUE(archive_key, canonical_post_key),
  FOREIGN KEY (source_key) REFERENCES sources(source_key),
  FOREIGN KEY (archive_key) REFERENCES archives(archive_key)
);

CREATE INDEX IF NOT EXISTS idx_sources_archive
  ON sources (archive_key, source_key);

CREATE INDEX IF NOT EXISTS idx_posts_archive_created_at
  ON posts (archive_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_posts_archive_upvotes
  ON posts (archive_key, upvotes DESC);

CREATE INDEX IF NOT EXISTS idx_posts_archive_comments
  ON posts (archive_key, comments DESC);

CREATE INDEX IF NOT EXISTS idx_posts_source_created_at
  ON posts (source_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_posts_source_upvotes
  ON posts (source_key, upvotes DESC);

CREATE INDEX IF NOT EXISTS idx_posts_source_comments
  ON posts (source_key, comments DESC);

CREATE TABLE IF NOT EXISTS crawl_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key TEXT NOT NULL,
  run_type TEXT NOT NULL,
  status TEXT NOT NULL,
  scanned_pages INTEGER NOT NULL DEFAULT 0,
  scanned_posts INTEGER NOT NULL DEFAULT 0,
  matched_posts INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  error_message TEXT,
  FOREIGN KEY (source_key) REFERENCES sources(source_key)
);

CREATE TABLE IF NOT EXISTS source_state (
  source_key TEXT PRIMARY KEY,
  head_anchor_history TEXT NOT NULL DEFAULT '[]',
  recovery_mode INTEGER NOT NULL DEFAULT 0,
  recovery_depth_hint INTEGER NOT NULL DEFAULT 1,
  backfill_anchor_post_id TEXT,
  backfill_anchor_created_at TEXT,
  backfill_page_hint INTEGER,
  blocked_until TEXT,
  last_blocked_at TEXT,
  last_block_reason TEXT,
  state_metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (source_key) REFERENCES sources(source_key)
);

CREATE TABLE IF NOT EXISTS coverage_intervals (
  source_key TEXT NOT NULL,
  oldest_post_id INTEGER NOT NULL,
  newest_post_id INTEGER NOT NULL,
  oldest_created_at TEXT NOT NULL DEFAULT '',
  newest_created_at TEXT NOT NULL DEFAULT '',
  checked_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (source_key, oldest_post_id, newest_post_id),
  CHECK (oldest_post_id > 0),
  CHECK (newest_post_id > 0),
  CHECK (oldest_post_id <= newest_post_id),
  FOREIGN KEY (source_key) REFERENCES sources(source_key)
);

CREATE INDEX IF NOT EXISTS idx_coverage_intervals_source_oldest
  ON coverage_intervals (source_key, oldest_post_id ASC);

CREATE INDEX IF NOT EXISTS idx_coverage_intervals_source_newest
  ON coverage_intervals (source_key, newest_post_id DESC);

CREATE TABLE IF NOT EXISTS coverage_absences (
  source_key TEXT NOT NULL,
  post_id INTEGER NOT NULL,
  newer_page INTEGER NOT NULL,
  older_page INTEGER NOT NULL,
  newer_boundary_post_id INTEGER NOT NULL,
  older_boundary_post_id INTEGER NOT NULL,
  checked_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (source_key, post_id),
  CHECK (post_id > 0),
  CHECK (newer_page > 0),
  CHECK (older_page > 0),
  CHECK (older_page = newer_page + 1),
  CHECK (newer_boundary_post_id > 0),
  CHECK (older_boundary_post_id > 0),
  CHECK (older_boundary_post_id < post_id),
  CHECK (post_id < newer_boundary_post_id),
  CHECK (older_boundary_post_id < newer_boundary_post_id),
  FOREIGN KEY (source_key) REFERENCES sources(source_key)
);

CREATE INDEX IF NOT EXISTS idx_coverage_absences_source_checked
  ON coverage_absences (source_key, checked_at DESC);

INSERT OR IGNORE INTO source_state (
  source_key,
  updated_at
)
SELECT
  source_key,
  CURRENT_TIMESTAMP
FROM sources;
