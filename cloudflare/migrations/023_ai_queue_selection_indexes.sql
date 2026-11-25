CREATE INDEX IF NOT EXISTS idx_ai_queue_pick
ON ai_practice_queue(source_key, work_kind, coalesce(last_attempt_at,''), discovered_at, item_id)
WHERE processed_instruction_version IS NULL OR CASE WHEN json_valid(descriptor_json) THEN json_type(descriptor_json,'$.metadata.followup_cursor')='object' ELSE 0 END;
