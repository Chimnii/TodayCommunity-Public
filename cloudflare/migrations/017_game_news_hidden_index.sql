-- Keep owner-only count/list reads proportional to hidden news, not all news.
CREATE INDEX IF NOT EXISTS idx_posts_game_news_hidden
  ON posts (archive_key, status, last_seen_at DESC, id DESC)
  WHERE archive_key = 'game-news' AND status = 'hidden';
