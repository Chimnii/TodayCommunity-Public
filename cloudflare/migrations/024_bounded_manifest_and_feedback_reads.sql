CREATE INDEX IF NOT EXISTS idx_game_posts_feedback_key
ON posts(external_post_id) WHERE archive_key='game-news';

CREATE INDEX IF NOT EXISTS idx_ai_manifest_pick
ON ai_practice_discovery_manifests(lane,source_key,coalesce(last_promoted_at,''),discovered_at,manifest_id)
WHERE status='pending' AND registration_offset<item_count;
