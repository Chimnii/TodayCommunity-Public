-- Backfill is evidence-derived; future publication is the database projection-time
-- observation. Apply this complete migration in one D1 transactional batch.
ALTER TABLE posts ADD COLUMN first_published_at TEXT;
CREATE TRIGGER ai_first_published_backfill_guard
BEFORE UPDATE OF first_published_at ON posts WHEN NEW.archive_key='ai-practice'
BEGIN
 SELECT CASE WHEN NEW.first_published_at IS NULL
  THEN RAISE(ABORT,'AI first publication requires valid include evaluation history') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NOT NULL)
  OR EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NULL)
  THEN RAISE(ABORT,'AI first publication requires valid include evaluation history') END;
END;
UPDATE posts SET first_published_at=(
 SELECT strftime('%Y-%m-%dT%H:%M:%fZ',min(julianday(e.evaluated_at)))
 FROM ai_practice_evaluations e WHERE e.item_id=substr(posts.canonical_post_key,13) AND 'ai-practice:'||e.item_id=posts.canonical_post_key
  AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
) WHERE archive_key='ai-practice';
DROP TRIGGER ai_first_published_backfill_guard;

-- Permanent guards also cover older collectors that omit the new column.
CREATE TRIGGER ai_first_published_insert_guard
BEFORE INSERT ON posts
WHEN NEW.archive_key='ai-practice' AND NEW.first_published_at IS NOT NULL
BEGIN
 SELECT RAISE(ABORT,'AI first publication is assigned by the database');
END;

CREATE TRIGGER ai_first_published_immutable
BEFORE UPDATE ON posts
WHEN OLD.archive_key='ai-practice' AND OLD.first_published_at IS NOT NULL
 AND NEW.first_published_at IS NOT OLD.first_published_at
BEGIN
 SELECT RAISE(ABORT,'AI first publication is immutable');
END;

CREATE TRIGGER ai_first_published_value_guard
BEFORE UPDATE OF first_published_at ON posts
WHEN NEW.archive_key='ai-practice'
BEGIN
 SELECT CASE WHEN NEW.first_published_at IS NULL
  THEN RAISE(ABORT,'AI first publication requires valid include evaluation history') END;
 SELECT CASE WHEN NEW.first_published_at IS NOT strftime('%Y-%m-%dT%H:%M:%fZ',NEW.first_published_at)
  THEN RAISE(ABORT,'AI first publication must be normalized UTC milliseconds') END;
 SELECT CASE WHEN OLD.first_published_at IS NULL AND NEW.first_published_at IS NOT strftime('%Y-%m-%dT%H:%M:%fZ','now')
  THEN RAISE(ABORT,'AI first publication is assigned by the database') END;
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NOT NULL)
  OR EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NULL)
  THEN RAISE(ABORT,'AI first publication requires valid include evaluation history') END;
END;

CREATE TRIGGER ai_first_published_on_insert
AFTER INSERT ON posts
WHEN NEW.archive_key='ai-practice' AND NEW.status='active' AND NEW.first_published_at IS NULL
BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NOT NULL)
  OR EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NULL)
  THEN RAISE(ABORT,'AI first publication requires valid include evaluation history') END;
 UPDATE posts SET first_published_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=NEW.id;
END;

CREATE TRIGGER ai_first_published_on_activation
AFTER UPDATE OF status,archive_key ON posts
WHEN NEW.archive_key='ai-practice' AND NEW.status='active' AND NEW.first_published_at IS NULL
BEGIN
 SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NOT NULL)
  OR EXISTS(SELECT 1 FROM ai_practice_evaluations e
  WHERE e.item_id=substr(NEW.canonical_post_key,13) AND 'ai-practice:'||e.item_id=NEW.canonical_post_key
   AND e.decision='include' AND e.collection_model NOT LIKE 'editorial%' AND e.filter_model NOT LIKE 'editorial%'
   AND julianday(e.evaluated_at) IS NULL)
  THEN RAISE(ABORT,'AI first publication requires valid include evaluation history') END;
 UPDATE posts SET first_published_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=NEW.id;
END;

CREATE INDEX idx_posts_ai_first_published ON posts(archive_key,first_published_at DESC,id DESC)
 WHERE archive_key='ai-practice' AND status='active';

-- Old decisions remain readable but cannot satisfy the new validity cache.
-- New evidence/assessment and its metadata are written with the decision.
ALTER TABLE ai_practice_candidates ADD COLUMN validity_json TEXT CHECK(validity_json IS NULL OR json_valid(validity_json));
ALTER TABLE ai_practice_candidates ADD COLUMN validity_checked_at TEXT;
ALTER TABLE ai_practice_candidates ADD COLUMN validity_expires_at TEXT;
ALTER TABLE ai_practice_candidates ADD COLUMN validity_version TEXT;
ALTER TABLE ai_practice_evaluations ADD COLUMN validity_json TEXT CHECK(validity_json IS NULL OR json_valid(validity_json));
ALTER TABLE ai_practice_evaluations ADD COLUMN validity_checked_at TEXT;
ALTER TABLE ai_practice_evaluations ADD COLUMN validity_expires_at TEXT;
ALTER TABLE ai_practice_evaluations ADD COLUMN validity_version TEXT;
