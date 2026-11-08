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
  first_published_at TEXT,
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
 validity_json TEXT CHECK(validity_json IS NULL OR json_valid(validity_json)),
 validity_checked_at TEXT,
 validity_expires_at TEXT,
 validity_version TEXT,
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
 validity_json TEXT CHECK(validity_json IS NULL OR json_valid(validity_json)),
 validity_checked_at TEXT,
 validity_expires_at TEXT,
 validity_version TEXT,
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
 (1, 'a06128439f495714c78ef4f992c23b95e5997b4243c044deed4551ae3c23dfde', '생활 비서·프로젝트 운영·모델 협의에 AI를 적용할 때 다른 작업에도 쓸 수 있는 구체적인 배움을 선별한다. 운영 경험과 설계 참고자료는 서로 독립적인 포함 경로다. 세 분야나 두 경로의 개수를 맞추지 않으며 좋은 자료가 없으면 포함 0개도 정상이다.

독립적인 두 포함 경로
- 운영 경험·실험: 어떤 문제나 업무를 AI에 맡겼는지, 선택한 구성·절차의 이유, 관찰한 현상과 수정·검증 과정에서 재사용할 배움이 드러나면 포함할 수 있다. 실패한 시도와 남은 한계도 중요한 결과이며 성공·장기 운용·정량 수치·공개 코드는 필수가 아니다.
- 설계 참고자료: 아직 제안·설계·프로토타입 단계라도 실제로 풀려는 문제, 구조 선택의 이유, 다른 대안과의 절충, 적용 조건·한계·실패 가능성이 구체적이면 포함할 수 있다. 이 경로에는 실제 운영 성과나 실행 증명을 추가로 요구하지 않는다. 문서·설계 토론·구조 설명서도 그 자체의 내용으로 판단한다.
- 두 경로를 모두 충족할 필요는 없다. 항목별 형식이나 특정 도구 이름보다 문제와 선택·제약 사이의 인과를 이해하고 다른 상황에 적용할 수 있는 깊이를 본다.
- 실제 관찰한 결과, 작성자의 자체 주장, 가상·실험실 시험, 아직 실행하지 않은 제안을 구분한다. 업체가 소개하는 사례도 같은 기준으로 판단하며 자체 보고를 독립 검증 결과처럼 취급하지 않는다.
- 요약에서 무엇을 왜 그렇게 했거나 하려는지, 그 선택이 어느 조건에서 성립하고 무엇을 포기하는지 이해할 수 있어야 한다. 도구 이름이나 기능을 나열하는 데 그치는 글은 충분하지 않다.

낮게 평가하거나 제외하는 자료
- 출시 홍보, 일반적인 설치 안내, 기능 목록, 막연한 성공담·미래 계획·목표 선언, 단순 질문·스터디 후기는 원칙적으로 제외한다. 다만 같은 글에 두 포함 경로 중 하나를 뒷받침하는 구체적 내용이 있으면 그 근거로 판단한다.
- 자잘한 버그 보고, 좁은 버전·옵션 호환 문제, 단순 기능 요청은 원칙적으로 제외한다. 다른 프로젝트에도 적용할 실패 원리·구조적 한계·대안의 절충을 설명하거나 구체적 실험으로 배움을 주면 포함할 수 있다. 재현 절차만 있다는 사실이나 제안 형식이라는 이유만으로 포함·제외하지 않는다.
- 공식 소개 카드만 있는 글도 같은 깊이 기준을 적용한다. 위임한 일 몇 가지를 열거하거나 성과만 주장하는 짧은 소개는 제외한다. 판단에 필요한 핵심 증거를 못 읽었으면 검토 대상으로 남긴다.
- 정적 매뉴얼·구조 문서·과거 사례는 참고자료로 표시한다. 구조 선택의 이유·대안·한계·실패 처리에서 배울 내용이 구체적이면 실제 성과가 없어도 포함할 수 있다. 일반 명령 목록은 충분하지 않으며 과거 자료를 새 기사처럼 취급하지 않는다.
- 주제와 무관한 일반 AI 전망·전략, 링크만 모은 목차, 동일 사례의 재게시, 인기나 조회 수만 높은 글은 제외한다.

모델 협의의 구분
- 단순 분업·병렬 호출과 모델 간 협의를 구분한다. 질문·반론·수정·교차 검토로 판단이 어떻게 바뀌었는지, 또는 협의가 실패한 이유가 구체적이면 가치가 높다.
- 투표·승자 선정·자유 토론 등 특정 방식 자체를 배척하지 않는다. 방식별 상호작용·판단 과정과 그 설계 이유·한계를 구체적으로 설명하면 설계 참고자료 경로로도 포함할 수 있다. 실제 관측한 상호작용과 제안된 절차를 구분하고 투표나 병렬 작업을 동등한 자유 대화라고 부르지 않는다.

판정
- include: 위 범위에서 핵심 배움이 구체적이며 읽은 자료가 이를 뒷받침한다.
- exclude: 읽은 자료의 내용이 범위·깊이 기준에 미달하거나 중복이다.
- review: 포함 여부를 정하는 데 필요한 자료가 부족하거나 사실·번역·분류에 해결되지 않은 의문이 있다. 접근 실패나 미처리를 제외 판정으로 바꾸지 않는다.

선별은 자료 자체의 깊이와 재사용성으로 판단한다. 개인의 비공개 프로젝트나 능력을 추정해 비교하지 않는다.

현재도 읽을 가치가 있는지
- 글이 오래됐거나 GPT-4 같은 이전 모델이 등장한다는 이유만으로 제외하지 않는다. 모델·도구가 달라져도 남는 구조 선택의 이유, 실패 원리, 검증 방법, 대안과 절충이 구체적이면 포함할 수 있다.
- 핵심이 현재 그대로 적용할 API·서비스·우회법·가격/성능 비교에 달려 있으면 같은 제품·API·버전 범위의 공식 문서·변경 이력·종료 안내로 확인한다. 최신 README나 링크의 생존만으로 모든 주장이 유효하다고 보지 않는다.
- 핵심 API가 제거됐거나 서비스가 종료됐고, 이미 해결된 제약을 피하는 낡은 우회법이나 더 이상 성립하지 않는 비교가 글의 중심이며 남는 배움도 부족하면 제외한다. 권장 중단이나 향후 종료 예고를 이미 종료된 것으로 바꾸지 않는다. 종료 예고는 판단 기준일과 효력일을 비교한다.
- 과거 장애가 수정됐어도 원인·설계 회고에서 배울 수 있으면 포함할 수 있다. 일부 설정·방식만 낡았고 핵심 가치는 남는다면 제한을 요약에 정확히 적고 포함한다. 원래의 관찰·주장을 현재 성능이나 독립 검증 결과처럼 표현하지 않는다.
- 필수 현재 근거를 확보하지 못했거나 제품·버전 범위가 맞지 않으면 검토 대상으로 남긴다. 읽지 못한 자료나 모델의 기억으로 지원·종료·비용·성능을 확정하지 않는다. 시점에 의존하지 않는 설계 참고자료에 불필요한 최신 지원 증명을 요구하지 않는다.', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 'bootstrap');
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

-- AI first-publication invariants (migration 022).
-- Permanent guards also cover older collectors that omit the new column.
CREATE TRIGGER ai_first_published_insert_guard
BEFORE INSERT ON posts
WHEN NEW.archive_key='ai-practice' AND NEW.first_published_at IS NOT NULL
BEGIN
 SELECT RAISE(ABORT,'AI first publication is assigned by the database');
END;

CREATE TRIGGER ai_first_published_immutable
BEFORE UPDATE ON posts
WHEN OLD.archive_key='ai-practice' AND OLD.first_published_at IS NOT NULL
 AND NEW.first_published_at IS NOT OLD.first_published_at
BEGIN
 SELECT RAISE(ABORT,'AI first publication is immutable');
END;

CREATE TRIGGER ai_first_published_value_guard
BEFORE UPDATE OF first_published_at ON posts
WHEN NEW.archive_key='ai-practice'
BEGIN
 SELECT CASE WHEN NEW.first_published_at IS NULL
  THEN RAISE(ABORT,'AI first publication requires valid include evaluation history') END;
 SELECT CASE WHEN NEW.first_published_at IS NOT strftime('%Y-%m-%dT%H:%M:%fZ',NEW.first_published_at)
  THEN RAISE(ABORT,'AI first publication must be normalized UTC milliseconds') END;
 SELECT CASE WHEN OLD.first_published_at IS NULL AND NEW.first_published_at IS NOT strftime('%Y-%m-%dT%H:%M:%fZ','now')
  THEN RAISE(ABORT,'AI first publication is assigned by the database') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NOT NULL)
  OR EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NULL)
  THEN RAISE(ABORT,'AI first publication requires valid include evaluation history') END;
END;

CREATE TRIGGER ai_first_published_on_insert
AFTER INSERT ON posts
WHEN NEW.archive_key='ai-practice' AND NEW.status='active' AND NEW.first_published_at IS NULL
BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NOT NULL)
  OR EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NULL)
  THEN RAISE(ABORT,'AI first publication requires valid include evaluation history') END;
 UPDATE posts SET first_published_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=NEW.id;
END;

CREATE TRIGGER ai_first_published_on_activation
AFTER UPDATE OF status,archive_key ON posts
WHEN NEW.archive_key='ai-practice' AND NEW.status='active' AND NEW.first_published_at IS NULL
BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NOT NULL)
  OR EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NULL)
  THEN RAISE(ABORT,'AI first publication requires valid include evaluation history') END;
 UPDATE posts SET first_published_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=NEW.id;
END;

CREATE INDEX idx_posts_ai_first_published ON posts(archive_key,first_published_at DESC,id DESC)
 WHERE archive_key='ai-practice' AND status='active';
