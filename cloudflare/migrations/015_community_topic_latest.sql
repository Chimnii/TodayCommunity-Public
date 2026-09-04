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
