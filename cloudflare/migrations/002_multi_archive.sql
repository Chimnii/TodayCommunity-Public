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

ALTER TABLE sources
ADD COLUMN archive_key TEXT NOT NULL DEFAULT 'dcinside-singularity';

UPDATE sources
SET archive_key = CASE source_key
  WHEN 'dcinside-agent-stack' THEN 'dcinside-agent-stack'
  WHEN 'fmkorea-best-munich-search' THEN 'fmkorea-munich'
  WHEN 'fmkorea-bayern-board' THEN 'fmkorea-munich'
  ELSE 'dcinside-singularity'
END;

ALTER TABLE posts
ADD COLUMN archive_key TEXT NOT NULL DEFAULT 'dcinside-singularity';

ALTER TABLE posts
ADD COLUMN canonical_post_key TEXT;

UPDATE posts
SET
  archive_key = CASE source_key
    WHEN 'dcinside-agent-stack' THEN 'dcinside-agent-stack'
    WHEN 'fmkorea-best-munich-search' THEN 'fmkorea-munich'
    WHEN 'fmkorea-bayern-board' THEN 'fmkorea-munich'
    ELSE 'dcinside-singularity'
  END,
  canonical_post_key = CASE source_key
    WHEN 'dcinside-singularity'
      THEN 'dcinside:thesingularity:' || external_post_id
    WHEN 'dcinside-agent-stack'
      THEN 'dcinside:agent_stack:' || external_post_id
    WHEN 'fmkorea-best-munich-search'
      THEN 'fmkorea:' || external_post_id
    WHEN 'fmkorea-bayern-board'
      THEN 'fmkorea:' || external_post_id
    ELSE source_key || ':' || external_post_id
  END;

CREATE INDEX IF NOT EXISTS idx_sources_archive
  ON sources (archive_key, source_key);

CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_archive_canonical
  ON posts (archive_key, canonical_post_key);

CREATE INDEX IF NOT EXISTS idx_posts_archive_created_at
  ON posts (archive_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_posts_archive_upvotes
  ON posts (archive_key, upvotes DESC);

CREATE INDEX IF NOT EXISTS idx_posts_archive_comments
  ON posts (archive_key, comments DESC);
