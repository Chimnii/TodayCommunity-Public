export const AUTH_STATE_GUEST = "guest";
export const AUTH_STATE_AUTHENTICATED = "authenticated";
export const AUTH_STATE_ADMIN = "admin";
export const OWNER_ACTOR = "owner:primary-v1";

export const AUTHENTICATED_COOKIE = "__Host-tc_authenticated";
export const ADMIN_COOKIE = "__Host-tc_admin";

export const AUTHENTICATED_SESSION_SECONDS = 30 * 24 * 60 * 60;
export const ADMIN_SESSION_SECONDS = 60 * 60;

const SESSION_VERSION = 1;
const SESSION_AUDIENCE = "todaycommunity";
const SECRET_LINK_TOKEN_PATTERN = /^[A-Za-z0-9_-]{43}$/u;
const SESSION_TOKEN_PATTERN = /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{43}$/u;
const VERIFIER_PATTERN = /^pbkdf2-sha256\$(\d{6,7})\$([A-Za-z0-9_-]{22})\$([A-Za-z0-9_-]{43})$/u;
const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder();

export class AuthConfigurationError extends Error {}

function runtimeCrypto() {
  if (!globalThis.crypto?.subtle || !globalThis.crypto?.getRandomValues) {
    throw new AuthConfigurationError("Web Crypto is unavailable");
  }
  return globalThis.crypto;
}

export function encodeBase64Url(bytes) {
  let binary = "";
  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index]);
  }
  return btoa(binary)
    .replace(/\+/gu, "-")
    .replace(/\//gu, "_")
    .replace(/=+$/gu, "");
}

export function decodeBase64Url(value, expectedLength = null) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/u.test(value)) {
    return null;
  }
  const paddingLength = (4 - (value.length % 4)) % 4;
  try {
    const binary = atob(
      value.replace(/-/gu, "+").replace(/_/gu, "/") + "=".repeat(paddingLength)
    );
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    return expectedLength === null || bytes.length === expectedLength ? bytes : null;
  } catch {
    return null;
  }
}

function bytesToHex(bytes) {
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

async function sha256Bytes(value) {
  return new Uint8Array(
    await runtimeCrypto().subtle.digest(
      "SHA-256",
      value instanceof Uint8Array ? value : textEncoder.encode(String(value))
    )
  );
}

function sessionSecretBytes(env) {
  const secret = decodeBase64Url(String(env?.TC_AUTH_SESSION_SECRET || ""), 32);
  if (!secret) {
    throw new AuthConfigurationError("TC_AUTH_SESSION_SECRET is missing or invalid");
  }
  return secret;
}

async function hmacBytes(secret, value) {
  const key = await runtimeCrypto().subtle.importKey(
    "raw",
    secret,
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  return new Uint8Array(
    await runtimeCrypto().subtle.sign(
      "HMAC",
      key,
      value instanceof Uint8Array ? value : textEncoder.encode(String(value))
    )
  );
}

function timingSafeEqual(left, right) {
  if (!(left instanceof Uint8Array) || !(right instanceof Uint8Array)) {
    return false;
  }
  if (left.length !== right.length) {
    return false;
  }
  if (typeof runtimeCrypto().subtle.timingSafeEqual === "function") {
    return runtimeCrypto().subtle.timingSafeEqual(left, right);
  }
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left[index] ^ right[index];
  }
  return difference === 0;
}

export function generateSecretLinkToken() {
  const bytes = new Uint8Array(32);
  runtimeCrypto().getRandomValues(bytes);
  return encodeBase64Url(bytes);
}

export async function hashSecretLinkToken(token) {
  if (!SECRET_LINK_TOKEN_PATTERN.test(String(token || ""))) {
    return null;
  }
  return bytesToHex(await sha256Bytes(token));
}

export async function createSessionToken(env, state, credentialId = null, nowMs = Date.now()) {
  if (![AUTH_STATE_AUTHENTICATED, AUTH_STATE_ADMIN].includes(state)) {
    throw new TypeError("Unsupported authenticated state");
  }
  if (
    state === AUTH_STATE_AUTHENTICATED
    && (!Number.isSafeInteger(credentialId) || credentialId < 1)
  ) {
    throw new TypeError("Authenticated sessions require a credential id");
  }
  const issuedAt = Math.floor(nowMs / 1000);
  const lifetime = state === AUTH_STATE_ADMIN
    ? ADMIN_SESSION_SECONDS
    : AUTHENTICATED_SESSION_SECONDS;
  const payload = {
    v: SESSION_VERSION,
    aud: SESSION_AUDIENCE,
    state,
    credential_id: state === AUTH_STATE_AUTHENTICATED ? credentialId : null,
    iat: issuedAt,
    exp: issuedAt + lifetime,
  };
  const encodedPayload = encodeBase64Url(textEncoder.encode(JSON.stringify(payload)));
  const signature = await hmacBytes(sessionSecretBytes(env), encodedPayload);
  return `${encodedPayload}.${encodeBase64Url(signature)}`;
}

async function verifySessionToken(token, env, expectedState, nowMs = Date.now()) {
  if (!SESSION_TOKEN_PATTERN.test(String(token || ""))) {
    return null;
  }
  const [encodedPayload, encodedSignature] = token.split(".");
  const suppliedSignature = decodeBase64Url(encodedSignature, 32);
  const payloadBytes = decodeBase64Url(encodedPayload);
  if (!suppliedSignature || !payloadBytes || payloadBytes.length > 1024) {
    return null;
  }
  const expectedSignature = await hmacBytes(sessionSecretBytes(env), encodedPayload);
  if (!timingSafeEqual(suppliedSignature, expectedSignature)) {
    return null;
  }

  let payload;
  try {
    payload = JSON.parse(textDecoder.decode(payloadBytes));
  } catch {
    return null;
  }
  const now = Math.floor(nowMs / 1000);
  const maximumLifetime = expectedState === AUTH_STATE_ADMIN
    ? ADMIN_SESSION_SECONDS
    : AUTHENTICATED_SESSION_SECONDS;
  if (
    !payload
    || payload.v !== SESSION_VERSION
    || payload.aud !== SESSION_AUDIENCE
    || payload.state !== expectedState
    || !Number.isSafeInteger(payload.iat)
    || !Number.isSafeInteger(payload.exp)
    || payload.iat > now + 60
    || payload.exp <= now
    || payload.exp - payload.iat !== maximumLifetime
  ) {
    return null;
  }
  if (
    expectedState === AUTH_STATE_AUTHENTICATED
    && (!Number.isSafeInteger(payload.credential_id) || payload.credential_id < 1)
  ) {
    return null;
  }
  if (expectedState === AUTH_STATE_ADMIN && payload.credential_id !== null) {
    return null;
  }
  return payload;
}

function parseCookies(request) {
  const cookies = new Map();
  for (const entry of String(request.headers.get("Cookie") || "").split(";")) {
    const separator = entry.indexOf("=");
    if (separator < 1) {
      continue;
    }
    const name = entry.slice(0, separator).trim();
    const value = entry.slice(separator + 1).trim();
    if (name && !cookies.has(name)) {
      cookies.set(name, value);
    }
  }
  return cookies;
}

export function sessionCookie(state, token) {
  const name = state === AUTH_STATE_ADMIN ? ADMIN_COOKIE : AUTHENTICATED_COOKIE;
  const maxAge = state === AUTH_STATE_ADMIN
    ? ADMIN_SESSION_SECONDS
    : AUTHENTICATED_SESSION_SECONDS;
  return `${name}=${token}; Path=/; Max-Age=${maxAge}; HttpOnly; Secure; SameSite=Strict`;
}

export function expiredSessionCookie(state) {
  const name = state === AUTH_STATE_ADMIN ? ADMIN_COOKIE : AUTHENTICATED_COOKIE;
  return `${name}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict`;
}

export function capabilitiesForState(state) {
  const authenticated = state === AUTH_STATE_AUTHENTICATED || state === AUTH_STATE_ADMIN;
  return {
    rate: authenticated,
    hide: authenticated,
    manage_rules: authenticated,
    manage_auth: state === AUTH_STATE_ADMIN,
  };
}

function identityForState(state, credentialId = null, cookiesToClear = []) {
  return {
    state,
    authenticated: state !== AUTH_STATE_GUEST,
    actor: state === AUTH_STATE_GUEST ? null : OWNER_ACTOR,
    credential_id: credentialId,
    capabilities: capabilitiesForState(state),
    cookies_to_clear: cookiesToClear,
  };
}

async function activeSecretLink(db, credentialId, nowIso) {
  if (!db?.prepare) {
    throw new AuthConfigurationError("D1 binding is unavailable");
  }
  const result = await db
    .prepare(
      `
      SELECT id
      FROM auth_secret_links
      WHERE id = ?
        AND revoked_at IS NULL
        AND (expires_at IS NULL OR expires_at > ?)
      LIMIT 1
      `
    )
    .bind(credentialId, nowIso)
    .all();
  return Array.isArray(result?.results) && result.results.length === 1;
}

export async function resolveAuthIdentity(request, env, nowMs = Date.now()) {
  sessionSecretBytes(env);
  const cookies = parseCookies(request);
  const cookiesToClear = [];
  const adminToken = cookies.get(ADMIN_COOKIE);
  if (adminToken) {
    const adminPayload = await verifySessionToken(
      adminToken,
      env,
      AUTH_STATE_ADMIN,
      nowMs
    );
    if (adminPayload) {
      return identityForState(AUTH_STATE_ADMIN, null, cookiesToClear);
    }
    cookiesToClear.push(expiredSessionCookie(AUTH_STATE_ADMIN));
  }

  const authenticatedToken = cookies.get(AUTHENTICATED_COOKIE);
  if (authenticatedToken) {
    const authenticatedPayload = await verifySessionToken(
      authenticatedToken,
      env,
      AUTH_STATE_AUTHENTICATED,
      nowMs
    );
    if (
      authenticatedPayload
      && await activeSecretLink(
        env.DB,
        authenticatedPayload.credential_id,
        new Date(nowMs).toISOString()
      )
    ) {
      return identityForState(
        AUTH_STATE_AUTHENTICATED,
        authenticatedPayload.credential_id,
        cookiesToClear
      );
    }
    cookiesToClear.push(expiredSessionCookie(AUTH_STATE_AUTHENTICATED));
  }
  return identityForState(AUTH_STATE_GUEST, null, cookiesToClear);
}

export async function verifyAdminPassword(password, verifier) {
  if (typeof password !== "string" || password.length < 16 || password.length > 200) {
    return false;
  }
  const match = VERIFIER_PATTERN.exec(String(verifier || ""));
  if (!match) {
    throw new AuthConfigurationError("TC_AUTH_ADMIN_VERIFIER is missing or invalid");
  }
  const iterations = Number(match[1]);
  const salt = decodeBase64Url(match[2], 16);
  const expected = decodeBase64Url(match[3], 32);
  if (!salt || !expected || iterations < 100000 || iterations > 1000000) {
    throw new AuthConfigurationError("TC_AUTH_ADMIN_VERIFIER is invalid");
  }
  const key = await runtimeCrypto().subtle.importKey(
    "raw",
    textEncoder.encode(password),
    "PBKDF2",
    false,
    ["deriveBits"]
  );
  const derived = new Uint8Array(
    await runtimeCrypto().subtle.deriveBits(
      { name: "PBKDF2", hash: "SHA-256", salt, iterations },
      key,
      256
    )
  );
  return timingSafeEqual(derived, expected);
}

export async function loginClientKeyHash(request, env) {
  const forwarded = String(request.headers.get("CF-Connecting-IP") || "")
    || String(request.headers.get("X-Forwarded-For") || "").split(",")[0].trim()
    || "unknown";
  return bytesToHex(
    await hmacBytes(sessionSecretBytes(env), `todaycommunity-login:${forwarded}`)
  );
}
