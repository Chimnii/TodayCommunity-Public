CREATE TABLE IF NOT EXISTS archives (
  archive_key TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  display_order INTEGER NOT NULL DEFAULT 0,
  is_public INTEGER NOT NULL DEFAULT 1 CHECK (is_public IN (0, 1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
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
    0,
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
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
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
  original_title TEXT,
  summary TEXT,
  publisher TEXT,
  content_kind TEXT,
  evidence_level TEXT,
  created_at TEXT NOT NULL,
  created_at_raw TEXT NOT NULL,
  created_at_basis TEXT NOT NULL DEFAULT 'source'
    CHECK (created_at_basis IN ('source', 'first_seen')),
  created_at_precision TEXT NOT NULL DEFAULT 'second'
    CHECK (created_at_precision IN ('second', 'minute', 'date')),
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

CREATE INDEX IF NOT EXISTS idx_posts_source_created_at
  ON posts (source_key, created_at DESC);

-- Page-sized archive reads need both the visibility predicate and the complete
-- keyset order in the index. Archive-prefixed variants serve one archive;
-- prefix-free variants serve the public all-archive feed without a sort scan.
CREATE INDEX IF NOT EXISTS idx_posts_archive_created_at
  ON posts (archive_key, created_at DESC, id DESC)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_posts_archive_upvotes
  ON posts (archive_key, upvotes DESC, created_at DESC, id DESC)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_posts_archive_comments
  ON posts (archive_key, comments DESC, created_at DESC, id DESC)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_posts_active_created
  ON posts (created_at DESC, id DESC)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_posts_active_upvotes
  ON posts (upvotes DESC, created_at DESC, id DESC)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_posts_active_comments
  ON posts (comments DESC, created_at DESC, id DESC)
  WHERE status = 'active';

-- Only hidden news rows participate; normal collection adds no index writes.
CREATE INDEX IF NOT EXISTS idx_posts_game_news_hidden
  ON posts (archive_key, status, last_seen_at DESC, id DESC)
  WHERE archive_key = 'game-news' AND status = 'hidden';

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

INSERT OR IGNORE INTO archive_stats (archive_key)
SELECT archive_key FROM archives;

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

CREATE TABLE IF NOT EXISTS community_topic_latest (
  archive_key TEXT PRIMARY KEY,
  run_id TEXT NOT NULL CHECK (length(run_id) BETWEEN 1 AND 128),
  generated_at TEXT NOT NULL CHECK (length(generated_at) BETWEEN 1 AND 64),
  state_hash TEXT NOT NULL CHECK (
    length(state_hash) = 64
    AND state_hash = lower(state_hash)
    AND state_hash NOT GLOB '*[^0-9a-f]*'
  ),
  payload_json TEXT NOT NULL CHECK (
    json_valid(payload_json)
    AND json_type(payload_json) = 'object'
  ),
  FOREIGN KEY (archive_key) REFERENCES archives(archive_key) ON DELETE CASCADE
) WITHOUT ROWID;

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
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
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
  created_at,
  updated_at
)
SELECT
  source_key,
  strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
  strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
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

CREATE TABLE IF NOT EXISTS auth_secret_link_archive_filters (
  secret_link_id INTEGER PRIMARY KEY,
  excluded_archive_keys_json TEXT NOT NULL DEFAULT '[]' CHECK (
    length(excluded_archive_keys_json) BETWEEN 2 AND 4096
    AND json_valid(excluded_archive_keys_json)
    AND json_type(excluded_archive_keys_json) = 'array'
  ),
  updated_at TEXT NOT NULL CHECK (length(updated_at) BETWEEN 1 AND 64),
  FOREIGN KEY (secret_link_id)
    REFERENCES auth_secret_links(id) ON DELETE CASCADE
);

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

-- One shared collection, independent of the secret link used to authenticate.
CREATE TABLE IF NOT EXISTS shared_favorites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  post_id INTEGER NOT NULL UNIQUE REFERENCES posts(id) ON DELETE CASCADE,
  saved_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

INSERT OR IGNORE INTO archives
  (archive_key, display_name, description, display_order, is_public, content_kind)
VALUES ('ai-practice', 'AI 실전', '생활 비서, 프로젝트 운영, 모델 협의의 실제 구축·운영 사례', 45, 1, 'article');

INSERT OR IGNORE INTO sources (source_key, archive_key, site_name, board_name, board_url) VALUES
 ('ai-practice-hermes', 'ai-practice', 'hermes', 'Hermes', 'https://hermes-agent.nousresearch.com/docs/user-stories'),
 ('ai-practice-paperclip', 'ai-practice', 'paperclip', 'Paperclip', 'https://github.com/paperclipai/paperclip/discussions'),
 ('ai-practice-openclaw', 'ai-practice', 'openclaw', 'OpenClaw', 'https://openclaw.ai/showcase'),
 ('ai-practice-gastown', 'ai-practice', 'gastown', 'Gas Town', 'https://yegge.ai/gastown'),
 ('ai-practice-pal', 'ai-practice', 'pal', 'PAL MCP', 'https://github.com/BeehiveInnovations/pal-mcp-server'),
 ('ai-practice-langchain', 'ai-practice', 'langchain', 'LangChain', 'https://www.langchain.com/blog');

INSERT OR IGNORE INTO archive_stats (archive_key, active_post_count, latest_seen_at, subject_options_json, stats_version, updated_at)
VALUES ('ai-practice', 0, '', '[]', 0, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

-- These are private collection ledgers. Public APIs select posts fields only.
CREATE TABLE IF NOT EXISTS ai_practice_candidates (
 item_id TEXT PRIMARY KEY,
 source_key TEXT NOT NULL REFERENCES sources(source_key),
 canonical_url TEXT NOT NULL UNIQUE,
 content_hash TEXT NOT NULL,
 instruction_version TEXT NOT NULL,
 case_key TEXT NOT NULL,
 original_title TEXT NOT NULL,
 title TEXT NOT NULL,
 summary TEXT NOT NULL,
 subject TEXT NOT NULL CHECK(subject IN ('생활 비서', '프로젝트 운영', '모델 협의')),
 evidence_url TEXT NOT NULL,
 evidence_level TEXT NOT NULL CHECK(evidence_level IN ('full_text', 'introduction')),
 content_kind TEXT NOT NULL CHECK(content_kind IN ('experience', 'reference', 'update')),
 published_at TEXT,
 published_at_raw TEXT,
 published_at_precision TEXT NOT NULL,
 first_seen_at TEXT NOT NULL,
 last_seen_at TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('include', 'exclude', 'review')),
 duplicate_of TEXT,
 policy_revision INTEGER NOT NULL DEFAULT 0,
 policy_hash TEXT NOT NULL DEFAULT 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
 post_id INTEGER REFERENCES posts(id)
);
CREATE INDEX IF NOT EXISTS idx_ai_practice_case ON ai_practice_candidates(case_key, post_id);
CREATE INDEX IF NOT EXISTS idx_ai_practice_duplicate ON ai_practice_candidates(duplicate_of);
CREATE TABLE IF NOT EXISTS ai_practice_evaluations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 item_id TEXT NOT NULL REFERENCES ai_practice_candidates(item_id),
 run_id TEXT NOT NULL,
 content_hash TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('include', 'exclude', 'review')),
 reason TEXT NOT NULL,
 evidence_quote TEXT NOT NULL,
 collection_model TEXT NOT NULL,
 filter_model TEXT NOT NULL,
 instruction_version TEXT NOT NULL,
 evaluated_at TEXT NOT NULL,
 duplicate_of TEXT,
 policy_revision INTEGER NOT NULL DEFAULT 0,
 policy_hash TEXT NOT NULL DEFAULT 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
 UNIQUE(item_id, run_id)
);
CREATE TABLE IF NOT EXISTS ai_practice_runs (
 run_id TEXT PRIMARY KEY,
 started_at TEXT NOT NULL,
 completed_at TEXT NOT NULL,
 scheduled INTEGER NOT NULL CHECK(scheduled IN (0, 1)),
 status TEXT NOT NULL CHECK(status IN ('success', 'partial', 'failed')),
 result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_practice_scheduled ON ai_practice_runs(scheduled, status, completed_at DESC);
CREATE TABLE IF NOT EXISTS ai_practice_source_state (
 source_key TEXT PRIMARY KEY REFERENCES sources(source_key),
 last_success_at TEXT,
 last_attempt_at TEXT NOT NULL,
 last_status TEXT NOT NULL
);

INSERT OR IGNORE INTO sources (source_key,archive_key,site_name,board_name,board_url) VALUES
 ('ai-practice-simon-willison','ai-practice','simon-willison','Simon Willison','https://simonwillison.net/atom/entries/'),
 ('ai-practice-anthropic','ai-practice','anthropic','Anthropic Engineering','https://www.anthropic.com/engineering'),
 ('ai-practice-gpters','ai-practice','gpters','GPTers','https://www.gpters.org/sitemap.xml'),
 ('ai-practice-latent-space','ai-practice','latent-space','Latent Space','https://latent.space/feeds/articles.xml'),
 ('ai-practice-massgen','ai-practice','massgen','MassGen','https://docs.massgen.ai/en/latest/examples/case_studies.html');

CREATE TABLE IF NOT EXISTS ai_practice_policy_snapshots (
 policy_revision INTEGER PRIMARY KEY,
 policy_hash TEXT NOT NULL,
 content TEXT NOT NULL,
 created_at TEXT NOT NULL,
 event_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS ai_practice_policy_state (
 id INTEGER PRIMARY KEY CHECK(id = 1),
 policy_revision INTEGER NOT NULL,
 policy_hash TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_practice_manual_rule_events (
 event_id TEXT PRIMARY KEY,
 base_revision INTEGER NOT NULL,
 policy_revision INTEGER NOT NULL,
 content TEXT NOT NULL,
 content_hash TEXT NOT NULL,
 payload_hash TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS ai_practice_rule_events_no_update BEFORE UPDATE ON ai_practice_manual_rule_events
BEGIN SELECT RAISE(ABORT, 'AI rule events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS ai_practice_rule_events_no_delete BEFORE DELETE ON ai_practice_manual_rule_events
BEGIN SELECT RAISE(ABORT, 'AI rule events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS ai_practice_policy_snapshots_no_update BEFORE UPDATE ON ai_practice_policy_snapshots
BEGIN SELECT RAISE(ABORT, 'AI policy snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ai_practice_policy_snapshots_no_delete BEFORE DELETE ON ai_practice_policy_snapshots
BEGIN SELECT RAISE(ABORT, 'AI policy snapshots are immutable'); END;

INSERT OR IGNORE INTO ai_practice_policy_snapshots VALUES
 (1, 'd98da633876781c7e71f28a54eaaf4f770bfa5c658a4e00a3d0ed498d783aa6e', '생활 비서·프로젝트 운영·모델 협의에 AI를 실제로 적용하면서 얻은 구체적이고 다른 작업에도 쓸 수 있는 배움을 선별한다. 세 분야의 개수를 맞추거나 빈칸을 채우지 않는다. 좋은 자료가 없으면 포함 0개도 정상이다.

우선하는 자료
- 어떤 실제 문제나 업무를 AI에 맡겼는지, 왜 그 구조를 택했는지, 구성·절차·결정의 이유가 드러나는 직접 운영기와 실험을 우선한다.
- 실패한 시도, 원인에 대한 관찰, 수정한 방식과 남은 한계도 중요한 결과다. 숫자 성과나 공개 코드가 없어도 구체적인 시행착오와 재사용 가능한 배움이 충분하면 포함한다.
- 실제 관찰한 결과, 작성자의 자체 주장, 가상·실험실 시험, 아직 실행하지 않은 제안을 구분한다. 업체가 소개하는 사례도 같은 기준으로 판단하며 자체 보고를 독립 검증 결과처럼 취급하지 않는다.
- 요약을 통해 무엇을 왜 그렇게 했고 무엇을 알게 됐는지 이해할 수 있어야 한다. 도구 이름이나 기능을 나열하는 데 그치는 글은 충분하지 않다.

낮게 평가하거나 제외하는 자료
- 출시 홍보, 일반적인 설치 안내, 기능 목록, 막연한 성공담·미래 계획·목표 선언, 단순 질문·스터디 후기는 원칙적으로 제외한다. 같은 글에 충분한 실제 실험과 운영 통찰이 있으면 그 근거로 판단한다.
- 단순 버그 보고, 좁은 버전·옵션 호환 문제, 기능 요청은 원칙적으로 제외한다. 다만 다른 프로젝트에도 적용할 수 있는 실패 원리와 검증·재현 과정 또는 구체적 실험이 있으면 포함할 수 있다. 재현 절차가 있다는 사실만으로 가치가 충분하다고 보지 않는다.
- 공식 소개 카드만 있는 글도 같은 깊이 기준을 적용한다. 위임한 일 몇 가지를 열거하거나 성과만 주장하는 짧은 소개는 제외한다. 판단에 필요한 핵심 증거를 못 읽었으면 검토 대상으로 남긴다.
- 정적 매뉴얼과 과거 사례는 참고자료다. 구조 선택의 이유·트레이드오프·실패 처리 등 따라가며 배울 수 있는 구체적 내용이 있어야 포함한다. 참고자료라는 이유로 선별 기준을 면제하거나 새 기사처럼 취급하지 않는다.
- 주제와 무관한 일반 AI 전망·전략, 링크만 모은 목차, 동일 사례의 재게시, 인기나 조회 수만 높은 글은 제외한다.

모델 협의의 구분
- 단순 분업·병렬 호출과 모델 간 협의를 구분한다. 질문·반론·수정·교차 검토로 판단이 어떻게 바뀌었는지, 또는 협의가 실패한 이유가 구체적이면 가치가 높다.
- 투표·승자 선정·자유 토론 등 특정 방식 자체를 배척하지 않는다. 방식별 실제 상호작용과 판단 과정, 검증 근거와 한계를 설명할 수 있어야 한다. 투표나 병렬 작업을 동등한 자유 대화라고 부르지 않는다.

판정
- include: 위 범위에서 핵심 배움이 구체적이며 읽은 자료가 이를 뒷받침한다.
- exclude: 읽은 자료의 내용이 범위·깊이 기준에 미달하거나 중복이다.
- review: 포함 여부를 정하는 데 필요한 자료가 부족하거나 사실·번역·분류에 해결되지 않은 의문이 있다. 접근 실패나 미처리를 제외 판정으로 바꾸지 않는다.

선별은 자료 자체의 깊이와 재사용성으로 판단한다. 개인의 비공개 프로젝트나 능력을 추정해 비교하지 않는다.', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 'bootstrap');
INSERT OR IGNORE INTO ai_practice_policy_state
 SELECT 1, policy_revision, policy_hash, created_at FROM ai_practice_policy_snapshots WHERE policy_revision = 1;

CREATE TABLE IF NOT EXISTS ai_practice_queue (
 item_id TEXT PRIMARY KEY,
 source_key TEXT NOT NULL REFERENCES sources(source_key),
 canonical_url TEXT NOT NULL UNIQUE,
 descriptor_json TEXT NOT NULL,
 discovery_hash TEXT NOT NULL DEFAULT '',
 work_kind TEXT NOT NULL DEFAULT 'recent' CHECK(work_kind IN ('recent','backfill','reassessment')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','retry','oversized','blocked','review','processed')),
 attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TEXT,
 last_attempt_at TEXT,
 last_error TEXT,
 discovered_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 cache_invalidated INTEGER NOT NULL DEFAULT 0 CHECK(cache_invalidated IN (0,1)),
 processed_policy_revision INTEGER,
 processed_policy_hash TEXT,
 processed_instruction_version TEXT,
 processed_content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_practice_queue_ready
 ON ai_practice_queue(status, next_attempt_at, work_kind, source_key, last_attempt_at, discovered_at);
CREATE INDEX IF NOT EXISTS idx_ai_practice_queue_source
 ON ai_practice_queue(source_key,work_kind,last_attempt_at);
CREATE TABLE IF NOT EXISTS ai_practice_discovery_manifests (
 manifest_id TEXT PRIMARY KEY,
 source_key TEXT NOT NULL REFERENCES sources(source_key),
 lane TEXT NOT NULL CHECK(lane IN ('recent','backfill')),
 descriptor_json TEXT NOT NULL CHECK(length(CAST(descriptor_json AS BLOB)) < 1048576),
 item_count INTEGER NOT NULL CHECK(item_count > 0 AND item_count <= 500),
 registration_offset INTEGER NOT NULL DEFAULT 0 CHECK(registration_offset >= 0 AND registration_offset <= item_count),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','complete')),
 discovered_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 last_promoted_at TEXT,
 last_cycle_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_practice_manifests_pending
 ON ai_practice_discovery_manifests(status,lane,source_key,last_promoted_at,discovered_at);

CREATE TABLE IF NOT EXISTS ai_practice_discovery_state (
 source_key TEXT NOT NULL REFERENCES sources(source_key),
 lane TEXT NOT NULL CHECK(lane IN ('recent','backfill')),
 cursor_json TEXT,
 scope_json TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL,
 complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)),
 discovered_count INTEGER NOT NULL DEFAULT 0,
 last_discovered_at TEXT NOT NULL,
 last_error TEXT,
 PRIMARY KEY(source_key,lane)
);

-- Include rejected/reviewed and owner-hidden rows: a new policy can reconsider
-- each candidate, while the public projection retains owner visibility states.
INSERT OR IGNORE INTO ai_practice_queue
 (item_id,source_key,canonical_url,descriptor_json,status,work_kind,discovered_at,updated_at)
 SELECT item_id,source_key,canonical_url,
 json_object('item_id',item_id,'source',substr(source_key,13),'canonical_url',canonical_url,
  'discovery_url',evidence_url,'evidence_url',evidence_url,'evidence_level',evidence_level,
  'original_title',original_title,'kind',CASE WHEN evidence_level='introduction' THEN 'showcase' ELSE 'article' END,
  'content_kind',content_kind,'published_at',published_at,'published_at_raw',published_at_raw,
  'published_at_precision',published_at_precision,'introduction_text','','metadata',json('{}')),
 'pending','reassessment',first_seen_at,last_seen_at FROM ai_practice_candidates;
