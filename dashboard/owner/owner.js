const statusElement = document.querySelector("#owner-status");
const homeLink = document.querySelector("#home-link");
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/u;
const DEFAULT_DESTINATION = "/?target=all";

function readSafeDestination() {
  const requested = new URLSearchParams(window.location.search).get("next");
  if (!requested) {
    return DEFAULT_DESTINATION;
  }
  try {
    const destination = new URL(requested, window.location.origin);
    const parameters = [...destination.searchParams.keys()];
    if (
      destination.origin !== window.location.origin ||
      destination.pathname !== "/" ||
      destination.hash ||
      destination.searchParams.get("target") !== "all" ||
      parameters.length !== 1 ||
      parameters[0] !== "target"
    ) {
      return DEFAULT_DESTINATION;
    }
    return `${destination.pathname}${destination.search}`;
  } catch {
    return DEFAULT_DESTINATION;
  }
}

function fail(message) {
  statusElement.textContent = message;
  statusElement.dataset.kind = "error";
  homeLink.hidden = false;
}

async function exchangeToken() {
  const hash = new URLSearchParams(window.location.hash.slice(1));
  const token = String(hash.get("token") || "");
  const destination = readSafeDestination();

  // Remove the credential from browser history and the address bar before any request.
  window.history.replaceState(null, "", "/owner/");

  if (!TOKEN_PATTERN.test(token)) {
    fail("유효한 시크릿 링크가 아닙니다. 발급받은 주소 전체를 다시 확인해 주세요.");
    return;
  }

  try {
    const response = await fetch("/api/auth/secret/exchange", {
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-TodayCommunity-Auth": "1",
      },
      body: JSON.stringify({ token }),
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok || payload?.state !== "authenticated") {
      throw new Error(
        typeof payload?.error === "string"
          ? payload.error
          : "유효하지 않거나 만료된 시크릿 링크입니다."
      );
    }
    statusElement.textContent = "인증되었습니다. 공개 페이지로 이동합니다.";
    window.location.replace(destination);
  } catch (error) {
    fail(error.message || "시크릿 링크를 확인하지 못했습니다.");
  }
}

void exchangeToken();
