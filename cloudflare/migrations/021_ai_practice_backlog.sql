-- Private discovery/evaluation backlog. No original article bodies are stored.
ALTER TABLE ai_practice_candidates ADD COLUMN policy_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_practice_candidates ADD COLUMN policy_hash TEXT NOT NULL DEFAULT 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';
ALTER TABLE ai_practice_evaluations ADD COLUMN policy_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_practice_evaluations ADD COLUMN policy_hash TEXT NOT NULL DEFAULT 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';

ALTER TABLE ai_practice_candidates ADD COLUMN duplicate_of TEXT;
ALTER TABLE ai_practice_evaluations ADD COLUMN duplicate_of TEXT;
CREATE INDEX IF NOT EXISTS idx_ai_practice_duplicate ON ai_practice_candidates(duplicate_of);

INSERT OR IGNORE INTO sources (source_key,archive_key,site_name,board_name,board_url) VALUES
 ('ai-practice-simon-willison','ai-practice','simon-willison','Simon Willison','https://simonwillison.net/atom/entries/'),
 ('ai-practice-anthropic','ai-practice','anthropic','Anthropic Engineering','https://www.anthropic.com/engineering'),
 ('ai-practice-gpters','ai-practice','gpters','GPTers','https://www.gpters.org/sitemap.xml'),
 ('ai-practice-latent-space','ai-practice','latent-space','Latent Space','https://latent.space/feeds/articles.xml'),
 ('ai-practice-massgen','ai-practice','massgen','MassGen','https://docs.massgen.ai/en/latest/examples/case_studies.html');

CREATE TABLE IF NOT EXISTS ai_practice_policy_snapshots (
 policy_revision INTEGER PRIMARY KEY,
 policy_hash TEXT NOT NULL,
 content TEXT NOT NULL,
 created_at TEXT NOT NULL,
 event_id TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS ai_practice_policy_state (
 id INTEGER PRIMARY KEY CHECK(id = 1),
 policy_revision INTEGER NOT NULL,
 policy_hash TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_practice_manual_rule_events (
 event_id TEXT PRIMARY KEY,
 base_revision INTEGER NOT NULL,
 policy_revision INTEGER NOT NULL,
 content TEXT NOT NULL,
 content_hash TEXT NOT NULL,
 payload_hash TEXT NOT NULL,
 actor TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS ai_practice_rule_events_no_update BEFORE UPDATE ON ai_practice_manual_rule_events
BEGIN SELECT RAISE(ABORT, 'AI rule events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS ai_practice_rule_events_no_delete BEFORE DELETE ON ai_practice_manual_rule_events
BEGIN SELECT RAISE(ABORT, 'AI rule events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS ai_practice_policy_snapshots_no_update BEFORE UPDATE ON ai_practice_policy_snapshots
BEGIN SELECT RAISE(ABORT, 'AI policy snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS ai_practice_policy_snapshots_no_delete BEFORE DELETE ON ai_practice_policy_snapshots
BEGIN SELECT RAISE(ABORT, 'AI policy snapshots are immutable'); END;

INSERT OR IGNORE INTO ai_practice_policy_snapshots VALUES
 (1, 'd98da633876781c7e71f28a54eaaf4f770bfa5c658a4e00a3d0ed498d783aa6e', '생활 비서·프로젝트 운영·모델 협의에 AI를 실제로 적용하면서 얻은 구체적이고 다른 작업에도 쓸 수 있는 배움을 선별한다. 세 분야의 개수를 맞추거나 빈칸을 채우지 않는다. 좋은 자료가 없으면 포함 0개도 정상이다.

우선하는 자료
- 어떤 실제 문제나 업무를 AI에 맡겼는지, 왜 그 구조를 택했는지, 구성·절차·결정의 이유가 드러나는 직접 운영기와 실험을 우선한다.
- 실패한 시도, 원인에 대한 관찰, 수정한 방식과 남은 한계도 중요한 결과다. 숫자 성과나 공개 코드가 없어도 구체적인 시행착오와 재사용 가능한 배움이 충분하면 포함한다.
- 실제 관찰한 결과, 작성자의 자체 주장, 가상·실험실 시험, 아직 실행하지 않은 제안을 구분한다. 업체가 소개하는 사례도 같은 기준으로 판단하며 자체 보고를 독립 검증 결과처럼 취급하지 않는다.
- 요약을 통해 무엇을 왜 그렇게 했고 무엇을 알게 됐는지 이해할 수 있어야 한다. 도구 이름이나 기능을 나열하는 데 그치는 글은 충분하지 않다.

낮게 평가하거나 제외하는 자료
- 출시 홍보, 일반적인 설치 안내, 기능 목록, 막연한 성공담·미래 계획·목표 선언, 단순 질문·스터디 후기는 원칙적으로 제외한다. 같은 글에 충분한 실제 실험과 운영 통찰이 있으면 그 근거로 판단한다.
- 단순 버그 보고, 좁은 버전·옵션 호환 문제, 기능 요청은 원칙적으로 제외한다. 다만 다른 프로젝트에도 적용할 수 있는 실패 원리와 검증·재현 과정 또는 구체적 실험이 있으면 포함할 수 있다. 재현 절차가 있다는 사실만으로 가치가 충분하다고 보지 않는다.
- 공식 소개 카드만 있는 글도 같은 깊이 기준을 적용한다. 위임한 일 몇 가지를 열거하거나 성과만 주장하는 짧은 소개는 제외한다. 판단에 필요한 핵심 증거를 못 읽었으면 검토 대상으로 남긴다.
- 정적 매뉴얼과 과거 사례는 참고자료다. 구조 선택의 이유·트레이드오프·실패 처리 등 따라가며 배울 수 있는 구체적 내용이 있어야 포함한다. 참고자료라는 이유로 선별 기준을 면제하거나 새 기사처럼 취급하지 않는다.
- 주제와 무관한 일반 AI 전망·전략, 링크만 모은 목차, 동일 사례의 재게시, 인기나 조회 수만 높은 글은 제외한다.

모델 협의의 구분
- 단순 분업·병렬 호출과 모델 간 협의를 구분한다. 질문·반론·수정·교차 검토로 판단이 어떻게 바뀌었는지, 또는 협의가 실패한 이유가 구체적이면 가치가 높다.
- 투표·승자 선정·자유 토론 등 특정 방식 자체를 배척하지 않는다. 방식별 실제 상호작용과 판단 과정, 검증 근거와 한계를 설명할 수 있어야 한다. 투표나 병렬 작업을 동등한 자유 대화라고 부르지 않는다.

판정
- include: 위 범위에서 핵심 배움이 구체적이며 읽은 자료가 이를 뒷받침한다.
- exclude: 읽은 자료의 내용이 범위·깊이 기준에 미달하거나 중복이다.
- review: 포함 여부를 정하는 데 필요한 자료가 부족하거나 사실·번역·분류에 해결되지 않은 의문이 있다. 접근 실패나 미처리를 제외 판정으로 바꾸지 않는다.

선별은 자료 자체의 깊이와 재사용성으로 판단한다. 개인의 비공개 프로젝트나 능력을 추정해 비교하지 않는다.', strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 'bootstrap');
INSERT OR IGNORE INTO ai_practice_policy_state
 SELECT 1, policy_revision, policy_hash, created_at FROM ai_practice_policy_snapshots WHERE policy_revision = 1;

CREATE TABLE IF NOT EXISTS ai_practice_queue (
 item_id TEXT PRIMARY KEY,
 source_key TEXT NOT NULL REFERENCES sources(source_key),
 canonical_url TEXT NOT NULL UNIQUE,
 descriptor_json TEXT NOT NULL,
 discovery_hash TEXT NOT NULL DEFAULT '',
 work_kind TEXT NOT NULL DEFAULT 'recent' CHECK(work_kind IN ('recent','backfill','reassessment')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','retry','oversized','blocked','review','processed')),
 attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TEXT,
 last_attempt_at TEXT,
 last_error TEXT,
 discovered_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 cache_invalidated INTEGER NOT NULL DEFAULT 0 CHECK(cache_invalidated IN (0,1)),
 processed_policy_revision INTEGER,
 processed_policy_hash TEXT,
 processed_instruction_version TEXT,
 processed_content_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_ai_practice_queue_ready
 ON ai_practice_queue(status, next_attempt_at, work_kind, source_key, last_attempt_at, discovered_at);
CREATE INDEX IF NOT EXISTS idx_ai_practice_queue_source
 ON ai_practice_queue(source_key,work_kind,last_attempt_at);
CREATE TABLE IF NOT EXISTS ai_practice_discovery_manifests (
 manifest_id TEXT PRIMARY KEY,
 source_key TEXT NOT NULL REFERENCES sources(source_key),
 lane TEXT NOT NULL CHECK(lane IN ('recent','backfill')),
 descriptor_json TEXT NOT NULL CHECK(length(CAST(descriptor_json AS BLOB)) < 1048576),
 item_count INTEGER NOT NULL CHECK(item_count > 0 AND item_count <= 500),
 registration_offset INTEGER NOT NULL DEFAULT 0 CHECK(registration_offset >= 0 AND registration_offset <= item_count),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','complete')),
 discovered_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 last_promoted_at TEXT,
 last_cycle_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_practice_manifests_pending
 ON ai_practice_discovery_manifests(status,lane,source_key,last_promoted_at,discovered_at);

CREATE TABLE IF NOT EXISTS ai_practice_discovery_state (
 source_key TEXT NOT NULL REFERENCES sources(source_key),
 lane TEXT NOT NULL CHECK(lane IN ('recent','backfill')),
 cursor_json TEXT,
 scope_json TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL,
 complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)),
 discovered_count INTEGER NOT NULL DEFAULT 0,
 last_discovered_at TEXT NOT NULL,
 last_error TEXT,
 PRIMARY KEY(source_key,lane)
);

-- Include rejected/reviewed and owner-hidden rows: a new policy can reconsider
-- each candidate, while the public projection retains owner visibility states.
INSERT OR IGNORE INTO ai_practice_queue
 (item_id,source_key,canonical_url,descriptor_json,status,work_kind,discovered_at,updated_at)
 SELECT item_id,source_key,canonical_url,
 json_object('item_id',item_id,'source',substr(source_key,13),'canonical_url',canonical_url,
  'discovery_url',evidence_url,'evidence_url',evidence_url,'evidence_level',evidence_level,
  'original_title',original_title,'kind',CASE WHEN evidence_level='introduction' THEN 'showcase' ELSE 'article' END,
  'content_kind',content_kind,'published_at',published_at,'published_at_raw',published_at_raw,
  'published_at_precision',published_at_precision,'introduction_text','','metadata',json('{}')),
 'pending','reassessment',first_seen_at,last_seen_at FROM ai_practice_candidates;
