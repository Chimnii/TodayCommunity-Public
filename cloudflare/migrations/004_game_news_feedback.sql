ALTER TABLE game_news_feedback
ADD COLUMN rating_level INTEGER CHECK (
  rating_level IS NULL OR rating_level IN (-2, -1, 1, 2)
);

ALTER TABLE game_news_evaluations
ADD COLUMN visibility_through_id INTEGER NOT NULL DEFAULT 0
  CHECK (visibility_through_id >= 0);

ALTER TABLE game_news_evaluations
ADD COLUMN manual_rules_through_id INTEGER NOT NULL DEFAULT 0
  CHECK (manual_rules_through_id >= 0);

CREATE TABLE game_news_visibility_events (
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

CREATE INDEX idx_game_news_visibility_candidate_id
  ON game_news_visibility_events (candidate_id, id DESC);

CREATE INDEX idx_game_news_visibility_created_at
  ON game_news_visibility_events (created_at DESC, id DESC);

CREATE TABLE game_news_manual_rule_events (
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

CREATE INDEX idx_game_news_manual_rules_key
  ON game_news_manual_rule_events (rule_key, id DESC);

CREATE INDEX idx_game_news_manual_rules_created_at
  ON game_news_manual_rule_events (created_at DESC, id DESC);

CREATE TRIGGER game_news_visibility_events_no_update
BEFORE UPDATE ON game_news_visibility_events
BEGIN
  SELECT RAISE(ABORT, 'game_news_visibility_events is append-only');
END;

CREATE TRIGGER game_news_visibility_events_no_delete
BEFORE DELETE ON game_news_visibility_events
BEGIN
  SELECT RAISE(ABORT, 'game_news_visibility_events is append-only');
END;

CREATE TRIGGER game_news_manual_rule_events_no_update
BEFORE UPDATE ON game_news_manual_rule_events
BEGIN
  SELECT RAISE(ABORT, 'game_news_manual_rule_events is append-only');
END;

CREATE TRIGGER game_news_manual_rule_events_no_delete
BEFORE DELETE ON game_news_manual_rule_events
BEGIN
  SELECT RAISE(ABORT, 'game_news_manual_rule_events is append-only');
END;
