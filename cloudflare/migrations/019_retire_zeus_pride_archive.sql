-- Retain collected posts and crawl state while retiring the public archive.
UPDATE archives
SET is_public = 0,
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE archive_key = 'dcinside-zeus-pride'
  AND is_public <> 0;
