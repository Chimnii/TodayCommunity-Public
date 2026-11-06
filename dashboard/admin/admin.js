const elements = {
  loginPanel: document.querySelector("#login-panel"),
  loginForm: document.querySelector("#login-form"),
  password: document.querySelector("#admin-password"),
  loginStatus: document.querySelector("#login-status"),
  managementPanel: document.querySelector("#management-panel"),
  logoutButton: document.querySelector("#logout-button"),
  createForm: document.querySelector("#create-form"),
  linkLabel: document.querySelector("#link-label"),
  linkExpiry: document.querySelector("#link-expiry"),
  createStatus: document.querySelector("#create-status"),
  secretResult: document.querySelector("#secret-result"),
  secretUrl: document.querySelector("#secret-url"),
  copyButton: document.querySelector("#copy-button"),
  refreshButton: document.querySelector("#refresh-button"),
  linksStatus: document.querySelector("#links-status"),
  linkList: document.querySelector("#link-list"),
  aiForm: document.querySelector("#ai-rules-form"),
  aiContent: document.querySelector("#ai-rules-content"),
  aiCount: document.querySelector("#ai-rules-count"),
  aiSave: document.querySelector("#ai-rules-save"),
  aiReload: document.querySelector("#ai-rules-reload"),
  aiRefresh: document.querySelector("#ai-status-refresh"),
  aiStatus: document.querySelector("#ai-rules-status"),
  aiMeta: document.querySelector("#ai-policy-meta"),
  aiApplication: document.querySelector("#ai-application-summary"),
  aiSources: document.querySelector("#ai-source-list"),
};
const aiRules = { active: false, epoch: 0, document: null, baseline: "", baseRevision: 0,
  busy: false, editVersion: 0, attempt: null };

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

function setStatus(element, message = "", kind = "") {
  element.textContent = message;
  if (kind) {
    element.dataset.kind = kind;
  } else {
    delete element.dataset.kind;
  }
}

async function requestJson(path, { method = "GET", body, write = false } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers["X-TodayCommunity-Auth"] = "1";
  }
  if (write) headers["X-TodayCommunity-Write"] = "1";
  const response = await fetch(path, {
    method,
    cache: "no-store",
    credentials: "same-origin",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    // The response status still provides a useful generic failure below.
  }
  if (!response.ok) {
    throw new ApiError(
      response.status,
      typeof payload?.error === "string" ? payload.error : "요청을 처리하지 못했습니다."
    );
  }
  return payload;
}

function showLogin(message = "") {
  clearAiRules();
  elements.loginPanel.hidden = false;
  elements.managementPanel.hidden = true;
  elements.secretResult.hidden = true;
  setStatus(elements.loginStatus, message, message ? "error" : "");
}

function showManagement() {
  aiRules.active = true;
  elements.loginPanel.hidden = true;
  elements.managementPanel.hidden = false;
  setStatus(elements.loginStatus);
}

function formatDate(value) {
  if (!value) {
    return "없음";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "알 수 없음";
  }
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function createMeta(text) {
  const item = document.createElement("span");
  item.textContent = text;
  return item;
}

function clearAiRules() {
  Object.assign(aiRules, { active: false, epoch: aiRules.epoch + 1, document: null,
    baseline: "", baseRevision: 0, busy: false, editVersion: 0, attempt: null });
  elements.aiContent.value = "";
  elements.aiMeta.replaceChildren();
  elements.aiSources.replaceChildren();
  elements.aiApplication.textContent = "관리자 인증 후 상태를 불러옵니다.";
  setStatus(elements.aiStatus);
  updateAiControls();
}

function updateAiControls() {
  const ready = aiRules.active && aiRules.document && !aiRules.busy;
  elements.aiContent.disabled = !ready;
  elements.aiSave.disabled = !ready;
  elements.aiReload.disabled = !aiRules.active || aiRules.busy;
  elements.aiRefresh.disabled = !aiRules.active || aiRules.busy;
  elements.aiCount.textContent = `${elements.aiContent.value.length.toLocaleString("ko-KR")} / 6,000자`;
}

function renderAiApplication(payload) {
  const policyDocument = payload.document;
  aiRules.document = policyDocument;
  elements.aiMeta.replaceChildren();
  for (const [label, value] of [["저장 버전", policyDocument.revision], ["마지막 기준 변경", formatDate(policyDocument.updated_at)], ["기준 해시", policyDocument.hash]]) {
    const term = document.createElement("dt");
    const description = document.createElement("dd");
    term.textContent = label;
    description.textContent = String(value);
    elements.aiMeta.append(term, description);
  }
  const application = payload.application || {};
  elements.aiApplication.textContent = `등록된 후보 ${application.total_count || 0}개 (중복 제외) · 등록 전 발견 항목 ${application.manifest_pending_count || 0}개 (중복 가능) · 현재 기준 미적용 ${application.stale_count || 0}개 · 처리 대기 ${application.pending_count || 0}개 (기준 재평가 ${application.reassessment_count || 0}개) · 실패·재시도 ${application.failed_count || 0}개 · 크기 초과 ${application.oversized_count || 0}개 · 검토 ${application.review_count || 0}개`;
  elements.aiSources.replaceChildren();
  const labels = { ok: "정상", success: "정상", processed: "처리됨", pending: "대기", retry: "재시도",
    failed: "실패", partial: "일부 처리", blocked: "접근 차단", not_started: "시작 전", complete: "범위 완료", running: "진행 중" };
  for (const source of application.sources || []) {
    const card = document.createElement("article");
    card.className = "ai-source-card";
    const title = document.createElement("h4");
    title.textContent = source.name || source.source_key;
    card.append(title);
    const backfill = source.backfill || {};
    for (const text of [
      `등록 후보 ${source.total_count || 0} (중복 제외) · 등록 전 발견 ${source.manifest_pending_count || 0} (중복 가능)`,
      `대기 ${source.pending_count || 0} · 실패·재시도 ${source.failed_count || 0} · 크기 초과 ${source.oversized_count || 0} · 검토 ${source.review_count || 0}`,
      `최근 수집 ${labels[source.last_status] || source.last_status || "시작 전"} · ${formatDate(source.last_attempt_at)}`,
      `과거 탐색 ${backfill.complete ? "기록 범위 완료" : labels[backfill.status] || backfill.status || "시작 전"}`,
      backfill.last_error ? `탐색 오류: ${backfill.last_error}` : "",
    ]) {
      if (!text) continue;
      const paragraph = document.createElement("p");
      paragraph.textContent = text;
      card.append(paragraph);
    }
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = "기록된 탐색 범위·재개 위치";
    const cursor = document.createElement("pre");
    cursor.textContent = `범위: ${backfill.scope_json || "기록 없음"}\n재개 위치: ${backfill.cursor_json || "기록 없음"}\n최근 탐색: ${formatDate(backfill.last_discovered_at)}`;
    details.append(summary, cursor);
    card.append(details);
    elements.aiSources.append(card);
  }
}

function adoptAiDocument(document) {
  elements.aiContent.value = String(document.content || "");
  aiRules.baseline = elements.aiContent.value;
  aiRules.baseRevision = document.revision;
  aiRules.attempt = null;
}

async function loadAiRules(replaceDraft = false) {
  if (!aiRules.active || aiRules.busy) return;
  aiRules.busy = true;
  const epoch = aiRules.epoch;
  const editVersion = aiRules.editVersion;
  const wasClean = elements.aiContent.value === aiRules.baseline;
  updateAiControls();
  setStatus(elements.aiStatus, "선정기준과 적용 상태를 불러오는 중입니다.");
  try {
    const payload = await requestJson("/api/ai-practice/rules");
    if (!aiRules.active || epoch !== aiRules.epoch) return;
    renderAiApplication(payload);
    if (editVersion === aiRules.editVersion && (replaceDraft || wasClean)) adoptAiDocument(payload.document);
    setStatus(elements.aiStatus, aiRules.baseRevision !== payload.document.revision
      ? "새 저장 버전이 있습니다. 편집 내용을 보관한 뒤 저장본을 불러와 주세요."
      : "저장된 기준과 현재 대기열을 불러왔습니다.");
  } catch (error) {
    if (epoch !== aiRules.epoch) return;
    if (error instanceof ApiError && error.status === 401) { showLogin("관리자 세션이 만료되었습니다. 다시 로그인해 주세요."); return; }
    setStatus(elements.aiStatus, error.message, "error");
  } finally {
    if (epoch === aiRules.epoch) { aiRules.busy = false; updateAiControls(); }
  }
}

async function saveAiRules() {
  if (!aiRules.active || !aiRules.document || aiRules.busy) return;
  const content = elements.aiContent.value.replace(/\r\n?/gu, "\n").trim();
  if (!content || content.length > 6000) { setStatus(elements.aiStatus, "선정기준을 1–6,000자로 입력해 주세요.", "error"); return; }
  if (!aiRules.attempt || aiRules.attempt.content !== content || aiRules.attempt.base_revision !== aiRules.baseRevision) {
    aiRules.attempt = { content, base_revision: aiRules.baseRevision, event_id: crypto.randomUUID() };
  }
  aiRules.busy = true;
  const epoch = aiRules.epoch;
  updateAiControls();
  setStatus(elements.aiStatus, "선정기준을 저장하는 중입니다.");
  try {
    const payload = await requestJson("/api/ai-practice/rules", { method: "POST", body: aiRules.attempt, write: true });
    if (!aiRules.active || epoch !== aiRules.epoch) return;
    renderAiApplication(payload);
    adoptAiDocument(payload.document);
    setStatus(elements.aiStatus, payload.save?.revision !== payload.document.revision
      ? "저장은 확인됐고, 이후 변경된 최신 저장본을 불러왔습니다."
      : payload.save?.changed ? "선정기준을 저장했습니다. 다음 수집부터 기존 후보를 순차 재평가합니다."
      : "내용이 같아 기준 버전을 유지했습니다. 재평가를 다시 시작하지 않습니다.", "success");
  } catch (error) {
    if (epoch !== aiRules.epoch) return;
    if (error instanceof ApiError && error.status === 401) { showLogin("관리자 세션이 만료되었습니다. 다시 로그인해 주세요."); return; }
    if (error instanceof ApiError && error.status === 409) aiRules.attempt = null;
    setStatus(elements.aiStatus, `${error.message} 편집 내용은 유지됩니다.`, "error");
  } finally {
    if (epoch === aiRules.epoch) { aiRules.busy = false; updateAiControls(); }
  }
}

function renderLinks(items) {
  elements.linkList.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "아직 발급된 시크릿 링크가 없습니다.";
    elements.linkList.append(empty);
    return;
  }

  for (const item of items) {
    const card = document.createElement("article");
    card.className = "link-card";
    card.dataset.active = String(Boolean(item.active));

    const copy = document.createElement("div");
    copy.className = "link-card-copy";
    const label = document.createElement("strong");
    label.textContent = String(item.label || `링크 ${item.id}`);
    const meta = document.createElement("div");
    meta.className = "link-meta";
    meta.append(
      createMeta(item.active ? "사용 가능" : item.revoked_at ? "폐기됨" : "만료됨"),
      createMeta(`발급 ${formatDate(item.created_at)}`),
      createMeta(`최근 사용 ${formatDate(item.last_used_at)}`),
      createMeta(item.expires_at ? `만료 ${formatDate(item.expires_at)}` : "자동 만료 없음")
    );
    copy.append(label, meta);
    card.append(copy);

    if (item.active) {
      const actions = document.createElement("div");
      actions.className = "link-card-actions";
      const revokeButton = document.createElement("button");
      revokeButton.className = "button button-danger";
      revokeButton.type = "button";
      revokeButton.textContent = "폐기";
      revokeButton.addEventListener("click", () => void revokeLink(item, revokeButton));
      actions.append(revokeButton);
      card.append(actions);
    }

    elements.linkList.append(card);
  }
}

async function loadLinks() {
  elements.refreshButton.disabled = true;
  setStatus(elements.linksStatus, "목록을 불러오는 중입니다.");
  try {
    const payload = await requestJson("/api/auth/admin/links");
    const items = Array.isArray(payload?.items) ? payload.items : [];
    renderLinks(items);
    setStatus(elements.linksStatus, `총 ${items.length}개의 링크가 있습니다.`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      showLogin("관리자 세션이 만료되었습니다. 다시 로그인해 주세요.");
      return;
    }
    setStatus(elements.linksStatus, error.message, "error");
  } finally {
    elements.refreshButton.disabled = false;
  }
}

async function revokeLink(item, button) {
  if (!window.confirm(`「${item.label}」 시크릿 링크를 폐기할까요?\n이미 인증된 세션도 다음 확인부터 사용할 수 없게 됩니다.`)) {
    return;
  }
  button.disabled = true;
  setStatus(elements.linksStatus, "링크를 폐기하는 중입니다.");
  try {
    const payload = await requestJson("/api/auth/admin/links/revoke", {
      method: "POST",
      body: { id: item.id },
    });
    renderLinks(Array.isArray(payload?.items) ? payload.items : []);
    setStatus(elements.linksStatus, "시크릿 링크를 폐기했습니다.", "success");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      showLogin("관리자 세션이 만료되었습니다. 다시 로그인해 주세요.");
      return;
    }
    button.disabled = false;
    setStatus(elements.linksStatus, error.message, "error");
  }
}

elements.loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = elements.loginForm.querySelector("button[type='submit']");
  const password = elements.password.value;
  elements.password.value = "";
  submitButton.disabled = true;
  setStatus(elements.loginStatus, "로그인 중입니다.");
  try {
    const session = await requestJson("/api/auth/admin/login", {
      method: "POST",
      body: { password },
    });
    if (session?.state !== "admin") {
      throw new Error("관리자 세션을 확인하지 못했습니다.");
    }
    showManagement();
    await Promise.allSettled([loadLinks(), loadAiRules(true)]);
  } catch (error) {
    showLogin(error.message);
    elements.password.focus();
  } finally {
    submitButton.disabled = false;
  }
});

elements.createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const submitButton = elements.createForm.querySelector("button[type='submit']");
  submitButton.disabled = true;
  elements.secretResult.hidden = true;
  setStatus(elements.createStatus, "시크릿 링크를 생성하는 중입니다.");
  try {
    const payload = await requestJson("/api/auth/admin/links", {
      method: "POST",
      body: {
        label: elements.linkLabel.value,
        expires_in_days: Number(elements.linkExpiry.value),
      },
    });
    elements.secretUrl.value = String(payload?.secret_url || "");
    elements.secretResult.hidden = false;
    elements.linkLabel.value = "";
    setStatus(elements.createStatus, "시크릿 링크를 생성했습니다. 아래 주소를 지금 저장하세요.", "success");
    await loadLinks();
    elements.secretUrl.focus();
    elements.secretUrl.select();
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) {
      showLogin("관리자 세션이 만료되었습니다. 다시 로그인해 주세요.");
      return;
    }
    setStatus(elements.createStatus, error.message, "error");
  } finally {
    submitButton.disabled = false;
  }
});

elements.copyButton.addEventListener("click", async () => {
  const value = elements.secretUrl.value;
  if (!value) {
    return;
  }
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    elements.secretUrl.focus();
    elements.secretUrl.select();
    document.execCommand("copy");
  }
  setStatus(elements.createStatus, "시크릿 링크를 클립보드에 복사했습니다.", "success");
});

elements.refreshButton.addEventListener("click", () => void loadLinks());
elements.aiContent.addEventListener("input", () => { aiRules.editVersion++; updateAiControls(); });
elements.aiForm.addEventListener("submit", event => { event.preventDefault(); void saveAiRules(); });
elements.aiReload.addEventListener("click", () => void loadAiRules(true));
elements.aiRefresh.addEventListener("click", () => void loadAiRules(false));

elements.logoutButton.addEventListener("click", async () => {
  elements.logoutButton.disabled = true;
  try {
    await requestJson("/api/auth/admin/logout", { method: "POST", body: {} });
  } catch {
    // Showing the login form is still correct when a stale session cannot log out.
  } finally {
    elements.logoutButton.disabled = false;
    showLogin();
    elements.password.focus();
  }
});

async function initialize() {
  try {
    const session = await requestJson("/api/auth/session");
    if (session?.state === "admin") {
      showManagement();
      await Promise.allSettled([loadLinks(), loadAiRules(true)]);
      return;
    }
    showLogin();
    elements.password.focus();
  } catch (error) {
    showLogin(`인증 상태를 확인하지 못했습니다: ${error.message}`);
  }
}

void initialize();
