(() => {
  "use strict";

  const preferenceKey = "tc-analytics-excluded";
  const loader = document.currentScript;
  const control = document.querySelector("#analytics-exclude");
  const status = document.querySelector("#analytics-preference-status");
  let excluded = false;
  let storageAvailable = true;
  let started = false;

  try {
    excluded = localStorage.getItem(preferenceKey) === "1";
  } catch {
    // If the preference cannot be read, do not accidentally count an opted-out browser.
    excluded = true;
    storageAvailable = false;
  }

  function showPreference() {
    document.documentElement.dataset.analytics = excluded ? "excluded" : "pending";
    if (control) {
      control.checked = excluded;
      control.disabled = false;
    }
    if (status) {
      status.textContent = storageAvailable
        ? excluded ? "이 브라우저의 방문은 집계하지 않습니다." : "이 브라우저의 방문을 집계합니다."
        : "설정을 저장할 수 없어 이번 방문은 집계하지 않습니다. 브라우저의 사이트 저장소 설정을 확인하세요.";
    }
  }

  function savePreference(value) {
    excluded = value;
    try {
      localStorage.setItem(preferenceKey, value ? "1" : "0");
      storageAvailable = true;
    } catch {
      excluded = true;
      storageAvailable = false;
    }
    showPreference();
  }

  // This fragment is a convenient first visit for owner/QA browsers. It never
  // reaches the server or creates another indexable query URL.
  if (window.location.hash === "#analytics=off") savePreference(true);
  showPreference();
  control?.addEventListener("change", () => savePreference(control.checked));
  window.addEventListener("storage", event => {
    if (event.key !== preferenceKey && event.key !== null) return;
    // Unload an already-started beacon in other open tabs after opting out.
    if (event.newValue === "1" && started) window.location.reload();
    else if (!started) {
      excluded = event.newValue === "1";
      showPreference();
    }
  });

  function setSession(session) {
    if (["authenticated", "admin"].includes(session?.authentication)) {
      savePreference(true);
      return;
    }
    const guest = session?.authentication === "guest" && session.actor === null
      && session.capabilities?.rate === false && session.capabilities?.manage_auth === false;
    if (started || excluded || !guest || navigator.webdriver
      || window.location.origin !== "https://todaycommunity.pages.dev"
      || loader?.dataset.track === "false") return;
    // Recheck in case another tab opted out while the session request was pending.
    try {
      if (localStorage.getItem(preferenceKey) === "1") {
        excluded = true;
        showPreference();
        return;
      }
    } catch { return; }
    started = true;
    const script = document.createElement("script");
    script.src = "https://static.cloudflareinsights.com/beacon.min.js";
    script.defer = true;
    // Public ingestion identifier from the existing Pages snippet, not an API credential.
    script.dataset.cfBeacon = JSON.stringify({ token: "00168bb617e94db4bd554982350b6500" });
    document.head.append(script);
    document.documentElement.dataset.analytics = "enabled";
  }

  window.tcAnalytics = Object.freeze({ setSession });
  if (loader?.hasAttribute("data-standalone")) {
    fetch("/api/game-news/session", { credentials: "same-origin", cache: "no-store" })
      .then(response => response.ok ? response.json() : null)
      .then(setSession)
      .catch(() => {});
  }
})();
