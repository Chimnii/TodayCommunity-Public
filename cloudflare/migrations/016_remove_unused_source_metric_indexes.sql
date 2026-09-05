-- Source-specific readers order by created_at. Public metric sorts use the
-- archive-prefixed partial indexes. Retain source identity and created indexes.
-- Run crawler.jobs.check_migration_budget with all collection workflows paused
-- before applying: rebuilding these indexes would write roughly two rows per post.
DROP INDEX IF EXISTS idx_posts_source_upvotes;
DROP INDEX IF EXISTS idx_posts_source_comments;
