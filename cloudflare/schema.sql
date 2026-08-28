CREATE TABLE IF NOT EXISTS archives (
  archive_key TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  display_order INTEGER NOT NULL DEFAULT 0,
  is_public INTEGER NOT NULL DEFAULT 1 CHECK (is_public IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  content_kind TEXT NOT NULL DEFAULT 'community'
    CHECK (content_kind IN ('community', 'article'))
);

INSERT OR IGNORE INTO archives (
  archive_key,
  display_name,
  description,
  display_order,
  is_public,
  content_kind
) VALUES
  (
    'dcinside-singularity',
    '특이점이 온다',
    '디시인사이드 특이점이 온다 갤러리 인기글',
    10,
    1,
    'community'
  ),
  (
    'dcinside-agent-stack',
    'AI 활용',
    '디시인사이드 AI 활용 갤러리 인기글',
    20,
    1,
    'community'
  ),
  (
    'dcinside-zeus-pride',
    '제우스 오만의 신',
    '디시인사이드 제우스 오만의 신 갤러리 인기글',
    25,
    1,
    'community'
  ),
  (
    'fmkorea-munich',
    '뮌헨',
    '에펨코리아의 뮌헨 관련 인기글',
    30,
    1,
    'community'
  ),
  (
    'game-news',
    '게임 뉴스',
    '인벤, 디스이즈게임, 게임메카, 게임인사이트에서 선별한 게임 뉴스',
    40,
    1,
    'article'
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

CREATE TABLE IF NOT EXISTS community_topics (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  archive_key TEXT NOT NULL,
  normalized_label TEXT NOT NULL CHECK (
    length(normalized_label) BETWEEN 1 AND 100
  ),
  label TEXT NOT NULL CHECK (
    length(trim(label)) BETWEEN 2 AND 40
  ),
  first_seen_at TEXT NOT NULL CHECK (length(first_seen_at) BETWEEN 1 AND 64),
  last_seen_at TEXT NOT NULL CHECK (length(last_seen_at) BETWEEN 1 AND 64),
  UNIQUE(archive_key, normalized_label),
  FOREIGN KEY (archive_key) REFERENCES archives(archive_key)
);

CREATE INDEX IF NOT EXISTS idx_community_topics_archive_seen
  ON community_topics (archive_key, last_seen_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS community_post_topic_analyses (
  post_id INTEGER PRIMARY KEY,
  archive_key TEXT NOT NULL,
  input_hash TEXT NOT NULL CHECK (
    length(input_hash) = 64
    AND input_hash = lower(input_hash)
    AND input_hash NOT GLOB '*[^0-9a-f]*'
  ),
  instruction_version TEXT NOT NULL CHECK (
    length(instruction_version) BETWEEN 1 AND 100
  ),
  model_id TEXT NOT NULL CHECK (length(model_id) BETWEEN 1 AND 200),
  analyzed_at TEXT NOT NULL CHECK (length(analyzed_at) BETWEEN 1 AND 64),
  FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
  FOREIGN KEY (archive_key) REFERENCES archives(archive_key)
);

CREATE INDEX IF NOT EXISTS idx_community_topic_analyses_archive_time
  ON community_post_topic_analyses (archive_key, analyzed_at DESC, post_id DESC);

CREATE TABLE IF NOT EXISTS community_post_topics (
  post_id INTEGER NOT NULL,
  topic_id INTEGER NOT NULL,
  topic_rank INTEGER NOT NULL CHECK (topic_rank BETWEEN 1 AND 3),
  assigned_at TEXT NOT NULL CHECK (length(assigned_at) BETWEEN 1 AND 64),
  PRIMARY KEY (post_id, topic_id),
  UNIQUE(post_id, topic_rank),
  FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
  FOREIGN KEY (topic_id) REFERENCES community_topics(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_community_post_topics_topic_post
  ON community_post_topics (topic_id, post_id);

CREATE TABLE IF NOT EXISTS community_topic_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  archive_key TEXT NOT NULL,
  run_id TEXT NOT NULL CHECK (length(run_id) BETWEEN 1 AND 128),
  window_start TEXT NOT NULL CHECK (length(window_start) BETWEEN 1 AND 64),
  window_end TEXT NOT NULL CHECK (length(window_end) BETWEEN 1 AND 64),
  window_hours INTEGER NOT NULL CHECK (window_hours BETWEEN 1 AND 168),
  generated_at TEXT NOT NULL CHECK (length(generated_at) BETWEEN 1 AND 64),
  input_hash TEXT NOT NULL CHECK (
    length(input_hash) = 64
    AND input_hash = lower(input_hash)
    AND input_hash NOT GLOB '*[^0-9a-f]*'
  ),
  summary_text TEXT NOT NULL CHECK (length(summary_text) BETWEEN 1 AND 500),
  eligible_post_count INTEGER NOT NULL CHECK (eligible_post_count >= 0),
  analyzed_post_count INTEGER NOT NULL CHECK (analyzed_post_count >= 0),
  UNIQUE(archive_key, run_id),
  FOREIGN KEY (archive_key) REFERENCES archives(archive_key)
);

CREATE INDEX IF NOT EXISTS idx_community_topic_snapshots_archive_time
  ON community_topic_snapshots (archive_key, generated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS community_topic_snapshot_items (
  snapshot_id INTEGER NOT NULL,
  topic_id INTEGER NOT NULL,
  topic_rank INTEGER NOT NULL CHECK (topic_rank BETWEEN 1 AND 6),
  post_count INTEGER NOT NULL CHECK (post_count >= 1),
  previous_post_count INTEGER NOT NULL CHECK (previous_post_count >= 0),
  hotness_score REAL NOT NULL CHECK (hotness_score >= 0),
  trend_state TEXT NOT NULL CHECK (
    trend_state IN ('new', 'rising', 'active')
  ),
  PRIMARY KEY (snapshot_id, topic_id),
  UNIQUE(snapshot_id, topic_rank),
  FOREIGN KEY (snapshot_id)
    REFERENCES community_topic_snapshots(id) ON DELETE CASCADE,
  FOREIGN KEY (topic_id) REFERENCES community_topics(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_community_topic_snapshot_items_rank
  ON community_topic_snapshot_items (snapshot_id, topic_rank);

CREATE TABLE IF NOT EXISTS community_topic_snapshot_representatives (
  snapshot_id INTEGER NOT NULL,
  topic_id INTEGER NOT NULL,
  post_id INTEGER NOT NULL,
  representative_rank INTEGER NOT NULL CHECK (representative_rank BETWEEN 1 AND 2),
  PRIMARY KEY (snapshot_id, topic_id, post_id),
  UNIQUE(snapshot_id, topic_id, representative_rank),
  FOREIGN KEY (snapshot_id)
    REFERENCES community_topic_snapshots(id) ON DELETE CASCADE,
  FOREIGN KEY (topic_id) REFERENCES community_topics(id) ON DELETE CASCADE,
  FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_community_topic_representatives_rank
  ON community_topic_snapshot_representatives (
    snapshot_id, topic_id, representative_rank
  );

CREATE TABLE IF NOT EXISTS game_news_candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  canonical_url TEXT NOT NULL UNIQUE
    CHECK (length(canonical_url) BETWEEN 1 AND 2048),
  url_sha256 TEXT NOT NULL UNIQUE
    CHECK (
      length(url_sha256) = 64
      AND url_sha256 = lower(url_sha256)
      AND url_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
  source_key TEXT NOT NULL,
  title TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 500),
  publisher TEXT NOT NULL CHECK (length(publisher) BETWEEN 1 AND 200),
  topic TEXT NOT NULL CHECK (length(topic) BETWEEN 1 AND 100),
  summary TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 1000),
  published_at TEXT CHECK (
    published_at IS NULL OR length(published_at) BETWEEN 1 AND 64
  ),
  published_at_raw TEXT CHECK (
    published_at_raw IS NULL OR length(published_at_raw) BETWEEN 1 AND 200
  ),
  discovery_reason TEXT NOT NULL
    CHECK (length(discovery_reason) BETWEEN 1 AND 500),
  first_seen_at TEXT NOT NULL CHECK (length(first_seen_at) BETWEEN 1 AND 64),
  last_seen_at TEXT NOT NULL CHECK (length(last_seen_at) BETWEEN 1 AND 64),
  first_run_id TEXT NOT NULL CHECK (length(first_run_id) BETWEEN 1 AND 128),
  last_run_id TEXT NOT NULL CHECK (length(last_run_id) BETWEEN 1 AND 128),
  current_evaluation_id INTEGER,
  FOREIGN KEY (source_key) REFERENCES sources(source_key),
  FOREIGN KEY (current_evaluation_id) REFERENCES game_news_evaluations(id)
);

CREATE INDEX IF NOT EXISTS idx_game_news_candidates_source_seen
  ON game_news_candidates (source_key, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_game_news_candidates_current_evaluation
  ON game_news_candidates (current_evaluation_id);

CREATE TABLE IF NOT EXISTS game_news_evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL,
  run_id TEXT NOT NULL CHECK (length(run_id) BETWEEN 1 AND 128),
  decision TEXT NOT NULL
    CHECK (decision IN ('include', 'exclude', 'review')),
  score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
  reason_code TEXT NOT NULL CHECK (length(reason_code) BETWEEN 1 AND 100),
  reason_summary TEXT NOT NULL
    CHECK (length(reason_summary) BETWEEN 1 AND 1000),
  model_id TEXT NOT NULL CHECK (length(model_id) BETWEEN 1 AND 200),
  instruction_version TEXT NOT NULL
    CHECK (length(instruction_version) BETWEEN 1 AND 100),
  instruction_hash TEXT NOT NULL
    CHECK (
      length(instruction_hash) = 64
      AND instruction_hash = lower(instruction_hash)
      AND instruction_hash NOT GLOB '*[^0-9a-f]*'
    ),
  preferences_hash TEXT NOT NULL
    CHECK (
      length(preferences_hash) = 64
      AND preferences_hash = lower(preferences_hash)
      AND preferences_hash NOT GLOB '*[^0-9a-f]*'
    ),
  feedback_through_id INTEGER NOT NULL DEFAULT 0
    CHECK (feedback_through_id >= 0),
  evaluated_at TEXT NOT NULL CHECK (length(evaluated_at) BETWEEN 1 AND 64),
  visibility_through_id INTEGER NOT NULL DEFAULT 0
    CHECK (visibility_through_id >= 0),
  manual_rules_through_id INTEGER NOT NULL DEFAULT 0
    CHECK (manual_rules_through_id >= 0),
  UNIQUE(candidate_id, run_id),
  FOREIGN KEY (candidate_id) REFERENCES game_news_candidates(id)
);

CREATE INDEX IF NOT EXISTS idx_game_news_evaluations_candidate_time
  ON game_news_evaluations (candidate_id, evaluated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS game_news_feedback (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL,
  evaluation_id INTEGER,
  feedback_type TEXT NOT NULL
    CHECK (feedback_type IN ('like', 'dislike', 'clear')),
  reason_code TEXT CHECK (
    reason_code IS NULL OR length(reason_code) BETWEEN 1 AND 100
  ),
  note TEXT CHECK (note IS NULL OR length(note) BETWEEN 1 AND 1000),
  actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 200),
  idempotency_key TEXT NOT NULL UNIQUE
    CHECK (length(idempotency_key) BETWEEN 1 AND 200),
  created_at TEXT NOT NULL CHECK (length(created_at) BETWEEN 1 AND 64),
  rating_level INTEGER CHECK (
    rating_level IS NULL OR rating_level IN (-2, -1, 1, 2)
  ),
  FOREIGN KEY (candidate_id) REFERENCES game_news_candidates(id),
  FOREIGN KEY (evaluation_id) REFERENCES game_news_evaluations(id)
);

CREATE INDEX IF NOT EXISTS idx_game_news_feedback_candidate_id
  ON game_news_feedback (candidate_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_game_news_feedback_created_at
  ON game_news_feedback (created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS game_news_visibility_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id INTEGER NOT NULL,
  evaluation_id INTEGER,
  action TEXT NOT NULL CHECK (action IN ('hide', 'restore')),
  actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 200),
  idempotency_key TEXT NOT NULL UNIQUE
    CHECK (length(idempotency_key) BETWEEN 1 AND 200),
  created_at TEXT NOT NULL CHECK (length(created_at) BETWEEN 1 AND 64),
  FOREIGN KEY (candidate_id) REFERENCES game_news_candidates(id),
  FOREIGN KEY (evaluation_id) REFERENCES game_news_evaluations(id)
);

CREATE INDEX IF NOT EXISTS idx_game_news_visibility_candidate_id
  ON game_news_visibility_events (candidate_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_game_news_visibility_created_at
  ON game_news_visibility_events (created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS game_news_manual_rule_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  rule_key TEXT NOT NULL CHECK (length(rule_key) BETWEEN 1 AND 100),
  action TEXT NOT NULL CHECK (action IN ('set', 'retract')),
  rule_text TEXT CHECK (
    rule_text IS NULL OR length(rule_text) BETWEEN 1 AND 1000
  ),
  strength TEXT CHECK (strength IS NULL OR strength IN ('soft', 'strong')),
  actor TEXT NOT NULL CHECK (length(actor) BETWEEN 1 AND 200),
  idempotency_key TEXT NOT NULL UNIQUE
    CHECK (length(idempotency_key) BETWEEN 1 AND 200),
  created_at TEXT NOT NULL CHECK (length(created_at) BETWEEN 1 AND 64),
  CHECK (
    (action = 'set' AND rule_text IS NOT NULL AND strength IS NOT NULL)
    OR
    (action = 'retract' AND rule_text IS NULL AND strength IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS idx_game_news_manual_rules_key
  ON game_news_manual_rule_events (rule_key, id DESC);

CREATE INDEX IF NOT EXISTS idx_game_news_manual_rules_created_at
  ON game_news_manual_rule_events (created_at DESC, id DESC);

CREATE TRIGGER IF NOT EXISTS game_news_evaluations_no_update
BEFORE UPDATE ON game_news_evaluations
BEGIN
  SELECT RAISE(ABORT, 'game_news_evaluations is append-only');
END;

CREATE TRIGGER IF NOT EXISTS game_news_evaluations_no_delete
BEFORE DELETE ON game_news_evaluations
BEGIN
  SELECT RAISE(ABORT, 'game_news_evaluations is append-only');
END;

CREATE TRIGGER IF NOT EXISTS game_news_feedback_no_update
BEFORE UPDATE ON game_news_feedback
BEGIN
  SELECT RAISE(ABORT, 'game_news_feedback is append-only');
END;

CREATE TRIGGER IF NOT EXISTS game_news_feedback_no_delete
BEFORE DELETE ON game_news_feedback
BEGIN
  SELECT RAISE(ABORT, 'game_news_feedback is append-only');
END;

CREATE TRIGGER IF NOT EXISTS game_news_visibility_events_no_update
BEFORE UPDATE ON game_news_visibility_events
BEGIN
  SELECT RAISE(ABORT, 'game_news_visibility_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS game_news_visibility_events_no_delete
BEFORE DELETE ON game_news_visibility_events
BEGIN
  SELECT RAISE(ABORT, 'game_news_visibility_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS game_news_manual_rule_events_no_update
BEFORE UPDATE ON game_news_manual_rule_events
BEGIN
  SELECT RAISE(ABORT, 'game_news_manual_rule_events is append-only');
END;

CREATE TRIGGER IF NOT EXISTS game_news_manual_rule_events_no_delete
BEFORE DELETE ON game_news_manual_rule_events
BEGIN
  SELECT RAISE(ABORT, 'game_news_manual_rule_events is append-only');
END;

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

CREATE INDEX IF NOT EXISTS idx_crawl_runs_source_status_id
  ON crawl_runs (source_key, status, id DESC);

CREATE INDEX IF NOT EXISTS idx_crawl_runs_source_id
  ON crawl_runs (source_key, id DESC);

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

-- Game-news sources do not use the community crawler's source_state table.
-- Keep this bootstrap after the legacy source_state initialization so a fresh
-- schema matches the additive migration without manufacturing cursor state.
INSERT OR IGNORE INTO sources (
  source_key,
  archive_key,
  site_name,
  board_name,
  board_url,
  min_upvotes,
  min_comments
) VALUES
  (
    'game-news-inven',
    'game-news',
    'inven',
    '인벤',
    'https://www.inven.co.kr/',
    0,
    0
  ),
  (
    'game-news-thisisgame',
    'game-news',
    'tig',
    '디스이즈게임',
    'https://www.thisisgame.com/',
    0,
    0
  ),
  (
    'game-news-gamemeca',
    'game-news',
    'gm',
    '게임메카',
    'https://www.gamemeca.com/',
    0,
    0
  ),
  (
    'game-news-gameinsight',
    'game-news',
    'gi',
    '게임인사이트',
    'https://www.gameinsight.co.kr/',
    0,
    0
  );

CREATE TABLE IF NOT EXISTS auth_secret_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  label TEXT NOT NULL CHECK (
    length(trim(label)) BETWEEN 1 AND 80
  ),
  token_hash TEXT NOT NULL UNIQUE CHECK (
    length(token_hash) = 64
    AND token_hash = lower(token_hash)
    AND token_hash NOT GLOB '*[^0-9a-f]*'
  ),
  created_at TEXT NOT NULL CHECK (length(created_at) BETWEEN 1 AND 64),
  last_used_at TEXT CHECK (
    last_used_at IS NULL OR length(last_used_at) BETWEEN 1 AND 64
  ),
  expires_at TEXT CHECK (
    expires_at IS NULL OR length(expires_at) BETWEEN 1 AND 64
  ),
  revoked_at TEXT CHECK (
    revoked_at IS NULL OR length(revoked_at) BETWEEN 1 AND 64
  )
);

CREATE INDEX IF NOT EXISTS idx_auth_secret_links_active
  ON auth_secret_links (revoked_at, expires_at, id DESC);

CREATE INDEX IF NOT EXISTS idx_auth_secret_links_last_used
  ON auth_secret_links (last_used_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS auth_login_limits (
  client_key_hash TEXT PRIMARY KEY CHECK (
    length(client_key_hash) = 64
    AND client_key_hash = lower(client_key_hash)
    AND client_key_hash NOT GLOB '*[^0-9a-f]*'
  ),
  failure_count INTEGER NOT NULL CHECK (failure_count BETWEEN 0 AND 1000),
  window_started_at TEXT NOT NULL
    CHECK (length(window_started_at) BETWEEN 1 AND 64),
  locked_until TEXT CHECK (
    locked_until IS NULL OR length(locked_until) BETWEEN 1 AND 64
  ),
  updated_at TEXT NOT NULL CHECK (length(updated_at) BETWEEN 1 AND 64)
);

CREATE INDEX IF NOT EXISTS idx_auth_login_limits_updated
  ON auth_login_limits (updated_at);
