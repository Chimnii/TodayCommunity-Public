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
};

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

async function requestJson(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    headers["X-TodayCommunity-Auth"] = "1";
  }
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
  elements.loginPanel.hidden = false;
  elements.managementPanel.hidden = true;
  elements.secretResult.hidden = true;
  setStatus(elements.loginStatus, message, message ? "error" : "");
}

function showManagement() {
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
    await loadLinks();
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
      await loadLinks();
      return;
    }
    showLogin();
    elements.password.focus();
  } catch (error) {
    showLogin(`인증 상태를 확인하지 못했습니다: ${error.message}`);
  }
}

void initialize();
