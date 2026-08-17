CREATE TABLE auth_secret_links (
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

CREATE INDEX idx_auth_secret_links_active
  ON auth_secret_links (revoked_at, expires_at, id DESC);

CREATE INDEX idx_auth_secret_links_last_used
  ON auth_secret_links (last_used_at DESC, id DESC);

CREATE TABLE auth_login_limits (
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

CREATE INDEX idx_auth_login_limits_updated
  ON auth_login_limits (updated_at);
