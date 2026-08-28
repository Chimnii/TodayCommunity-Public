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
