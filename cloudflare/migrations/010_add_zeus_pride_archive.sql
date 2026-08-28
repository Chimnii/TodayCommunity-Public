INSERT INTO archives (
  archive_key,
  display_name,
  description,
  display_order,
  is_public,
  content_kind
) VALUES (
  'dcinside-zeus-pride',
  '제우스 오만의 신',
  '디시인사이드 제우스 오만의 신 갤러리 인기글',
  25,
  1,
  'community'
)
ON CONFLICT(archive_key) DO UPDATE SET
  display_name = excluded.display_name,
  description = excluded.description,
  display_order = excluded.display_order,
  is_public = excluded.is_public,
  content_kind = excluded.content_kind,
  updated_at = CURRENT_TIMESTAMP;
