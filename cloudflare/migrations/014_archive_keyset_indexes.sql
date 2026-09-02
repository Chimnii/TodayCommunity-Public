-- Rebuild the archive-prefixed indexes separately from the statistics/global
-- index migration so each D1 daily write window has a bounded index workload.
DROP INDEX IF EXISTS idx_posts_archive_created_at;

CREATE INDEX IF NOT EXISTS idx_posts_archive_created_at
  ON posts (archive_key, created_at DESC, id DESC)
  WHERE status = 'active';

DROP INDEX IF EXISTS idx_posts_archive_upvotes;

CREATE INDEX IF NOT EXISTS idx_posts_archive_upvotes
  ON posts (archive_key, upvotes DESC, created_at DESC, id DESC)
  WHERE status = 'active';

DROP INDEX IF EXISTS idx_posts_archive_comments;

CREATE INDEX IF NOT EXISTS idx_posts_archive_comments
  ON posts (archive_key, comments DESC, created_at DESC, id DESC)
  WHERE status = 'active';
