ALTER TABLE posts ADD COLUMN original_title TEXT;
ALTER TABLE posts ADD COLUMN summary TEXT;
ALTER TABLE posts ADD COLUMN publisher TEXT;
ALTER TABLE posts ADD COLUMN content_kind TEXT;
ALTER TABLE posts ADD COLUMN evidence_level TEXT;

INSERT OR IGNORE INTO archives
  (archive_key, display_name, description, display_order, is_public, content_kind)
VALUES ('ai-practice', 'AI 실전', '생활 비서, 프로젝트 운영, 모델 협의의 실제 구축·운영 사례', 45, 1, 'article');

INSERT OR IGNORE INTO sources (source_key, archive_key, site_name, board_name, board_url) VALUES
 ('ai-practice-hermes', 'ai-practice', 'hermes', 'Hermes', 'https://hermes-agent.nousresearch.com/docs/user-stories'),
 ('ai-practice-paperclip', 'ai-practice', 'paperclip', 'Paperclip', 'https://github.com/paperclipai/paperclip/discussions'),
 ('ai-practice-openclaw', 'ai-practice', 'openclaw', 'OpenClaw', 'https://openclaw.ai/showcase'),
 ('ai-practice-gastown', 'ai-practice', 'gastown', 'Gas Town', 'https://yegge.ai/gastown'),
 ('ai-practice-pal', 'ai-practice', 'pal', 'PAL MCP', 'https://github.com/BeehiveInnovations/pal-mcp-server'),
 ('ai-practice-langchain', 'ai-practice', 'langchain', 'LangChain', 'https://www.langchain.com/blog');

INSERT OR IGNORE INTO archive_stats (archive_key, active_post_count, latest_seen_at, subject_options_json, stats_version, updated_at)
VALUES ('ai-practice', 0, '', '[]', 0, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

-- These are private collection ledgers. Public APIs select posts fields only.
CREATE TABLE IF NOT EXISTS ai_practice_candidates (
 item_id TEXT PRIMARY KEY,
 source_key TEXT NOT NULL REFERENCES sources(source_key),
 canonical_url TEXT NOT NULL UNIQUE,
 content_hash TEXT NOT NULL,
 instruction_version TEXT NOT NULL,
 case_key TEXT NOT NULL,
 original_title TEXT NOT NULL,
 title TEXT NOT NULL,
 summary TEXT NOT NULL,
 subject TEXT NOT NULL CHECK(subject IN ('생활 비서', '프로젝트 운영', '모델 협의')),
 evidence_url TEXT NOT NULL,
 evidence_level TEXT NOT NULL CHECK(evidence_level IN ('full_text', 'introduction')),
 content_kind TEXT NOT NULL CHECK(content_kind IN ('experience', 'reference', 'update')),
 published_at TEXT,
 published_at_raw TEXT,
 published_at_precision TEXT NOT NULL,
 first_seen_at TEXT NOT NULL,
 last_seen_at TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('include', 'exclude', 'review')),
 post_id INTEGER REFERENCES posts(id)
);
CREATE INDEX IF NOT EXISTS idx_ai_practice_case ON ai_practice_candidates(case_key, post_id);
CREATE TABLE IF NOT EXISTS ai_practice_evaluations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 item_id TEXT NOT NULL REFERENCES ai_practice_candidates(item_id),
 run_id TEXT NOT NULL,
 content_hash TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('include', 'exclude', 'review')),
 reason TEXT NOT NULL,
 evidence_quote TEXT NOT NULL,
 collection_model TEXT NOT NULL,
 filter_model TEXT NOT NULL,
 instruction_version TEXT NOT NULL,
 evaluated_at TEXT NOT NULL,
 UNIQUE(item_id, run_id)
);
CREATE TABLE IF NOT EXISTS ai_practice_runs (
 run_id TEXT PRIMARY KEY,
 started_at TEXT NOT NULL,
 completed_at TEXT NOT NULL,
 scheduled INTEGER NOT NULL CHECK(scheduled IN (0, 1)),
 status TEXT NOT NULL CHECK(status IN ('success', 'partial', 'failed')),
 result_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_practice_scheduled ON ai_practice_runs(scheduled, status, completed_at DESC);
CREATE TABLE IF NOT EXISTS ai_practice_source_state (
 source_key TEXT PRIMARY KEY REFERENCES sources(source_key),
 last_success_at TEXT,
 last_attempt_at TEXT NOT NULL,
 last_status TEXT NOT NULL
);
