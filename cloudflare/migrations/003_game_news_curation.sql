ALTER TABLE archives
ADD COLUMN content_kind TEXT NOT NULL DEFAULT 'community'
  CHECK (content_kind IN ('community', 'article'));

INSERT INTO archives (
  archive_key,
  display_name,
  description,
  display_order,
  is_public,
  created_at,
  updated_at,
  content_kind
) VALUES (
  'game-news',
  '게임 뉴스',
  '인벤과 디스이즈게임에서 선별한 게임 뉴스',
  40,
  1,
  CURRENT_TIMESTAMP,
  CURRENT_TIMESTAMP,
  'article'
)
ON CONFLICT(archive_key) DO UPDATE SET
  display_name = excluded.display_name,
  description = excluded.description,
  display_order = excluded.display_order,
  is_public = excluded.is_public,
  updated_at = excluded.updated_at,
  content_kind = excluded.content_kind;

INSERT INTO sources (
  source_key,
  archive_key,
  site_name,
  board_name,
  board_url,
  min_upvotes,
  min_comments,
  created_at,
  updated_at
) VALUES
  (
    'game-news-inven',
    'game-news',
    'inven',
    '인벤',
    'https://www.inven.co.kr/',
    0,
    0,
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
  ),
  (
    'game-news-thisisgame',
    'game-news',
    'thisisgame',
    '디스이즈게임',
    'https://www.thisisgame.com/',
    0,
    0,
    CURRENT_TIMESTAMP,
    CURRENT_TIMESTAMP
  )
ON CONFLICT(source_key) DO UPDATE SET
  archive_key = excluded.archive_key,
  site_name = excluded.site_name,
  board_name = excluded.board_name,
  board_url = excluded.board_url,
  min_upvotes = excluded.min_upvotes,
  min_comments = excluded.min_comments,
  updated_at = excluded.updated_at;

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
  FOREIGN KEY (candidate_id) REFERENCES game_news_candidates(id),
  FOREIGN KEY (evaluation_id) REFERENCES game_news_evaluations(id)
);

CREATE INDEX IF NOT EXISTS idx_game_news_feedback_candidate_id
  ON game_news_feedback (candidate_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_game_news_feedback_created_at
  ON game_news_feedback (created_at DESC, id DESC);

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
