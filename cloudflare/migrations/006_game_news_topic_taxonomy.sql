UPDATE game_news_candidates
SET topic = 'store'
WHERE topic = 'platform';

UPDATE game_news_candidates
SET topic = 'other'
WHERE topic = 'security';

UPDATE posts
SET subject = 'store'
WHERE archive_key = 'game-news'
  AND subject = 'platform';

UPDATE posts
SET subject = 'other'
WHERE archive_key = 'game-news'
  AND subject = 'security';
