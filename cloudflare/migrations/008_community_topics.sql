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
