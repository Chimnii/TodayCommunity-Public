import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const [
  publicHtml,
  publicApp,
  publicCss,
  adminHtml,
  adminApp,
  ownerHtml,
  ownerApp,
  headers,
  authLibrary,
  authRoute,
  syncScript,
  migration,
] = await Promise.all([
  readFile(new URL("../dashboard/index.html", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/app.js", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/styles.css", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/admin/index.html", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/admin/admin.js", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/owner/index.html", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/owner/owner.js", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/_headers", import.meta.url), "utf8"),
  readFile(new URL("../functions/api/_auth.js", import.meta.url), "utf8"),
  readFile(new URL("../functions/api/auth/[[path]].js", import.meta.url), "utf8"),
  readFile(new URL("../scripts/sync_public_mirror.ps1", import.meta.url), "utf8").catch((error) => {
    if (error?.code === "ENOENT") {
      return null;
    }
    throw error;
  }),
  readFile(new URL("../cloudflare/migrations/007_owner_auth.sql", import.meta.url), "utf8"),
]);

test("keeps authentication entry points off the public page", () => {
  assert.match(publicHtml, /<body data-auth-state="guest">/u);
  assert.doesNotMatch(publicHtml, /href="\/admin\/?"/u);
  assert.doesNotMatch(publicHtml, />\s*(?:로그인|관리자)\s*</u);
});

test("gates existing owner controls with guest, authenticated, and admin state", () => {
  assert.match(publicApp, /\["authenticated", "admin"\]\.includes/u);
  assert.match(publicApp, /document\.body\.dataset\.authState = authentication/u);
  assert.match(publicApp, /!articleMode \|\| !canUseFeedback/u);
  assert.match(publicApp, /isArticleArchive\(\) && state\.feedbackSession\?\.capabilities\?\.rate/u);
  assert.match(publicApp, /!isArticleArchive\(\) \|\| !state\.feedbackSession\?\.capabilities\?\.rate/u);
  assert.match(
    publicCss,
    /body\[data-content-kind="article"\]\[data-auth-state="guest"\] \.cell-feedback\s*\{[^}]*display:\s*none/su
  );
  assert.match(publicCss, /\.game-news-tools\[hidden\]\s*\{[^}]*display:\s*none/su);
});

test("serves a password-only admin manager at the unlinked admin route", () => {
  assert.match(adminHtml, /id="login-form"/u);
  assert.match(adminHtml, /id="admin-password"[^>]*type="password"/su);
  assert.doesNotMatch(adminHtml, /(?:name|id)="(?:user|username|email)"/iu);
  assert.match(adminHtml, /id="management-panel"[^>]*hidden/u);
  assert.match(adminHtml, /id="create-form"/u);
  assert.match(adminHtml, /id="link-list"/u);
  assert.match(adminApp, /\/api\/auth\/admin\/login/u);
  assert.match(adminApp, /\/api\/auth\/admin\/links\/revoke/u);
  assert.match(adminApp, /X-TodayCommunity-Auth/u);
  assert.doesNotMatch(`${adminHtml}\n${adminApp}`, /google|oauth|openid/iu);
});

test("exchanges secret-link fragments without exposing admin management", () => {
  assert.match(ownerApp, /window\.location\.hash/u);
  assert.match(ownerApp, /window\.history\.replaceState\(null, "", "\/owner\/"\)/u);
  assert.match(ownerApp, /\/api\/auth\/secret\/exchange/u);
  assert.match(ownerApp, /window\.location\.replace\("\/"\)/u);
  assert.ok(
    ownerApp.indexOf("window.history.replaceState") < ownerApp.indexOf("fetch("),
    "The fragment credential must be removed before the network request."
  );
  assert.doesNotMatch(`${ownerHtml}\n${ownerApp}`, /\/admin/u);
  assert.doesNotMatch(ownerApp, /localStorage|sessionStorage/u);
});

test("uses signed HttpOnly sessions and stores only hashed link credentials", () => {
  assert.match(authLibrary, /__Host-tc_authenticated/u);
  assert.match(authLibrary, /__Host-tc_admin/u);
  assert.match(authLibrary, /HttpOnly; Secure; SameSite=Strict/u);
  assert.match(authLibrary, /PBKDF2/u);
  assert.match(authLibrary, /HMAC/u);
  assert.match(authRoute, /hashSecretLinkToken\(rawToken\)/u);
  assert.match(authRoute, /secretUrl\.hash = `token=/u);
  assert.doesNotMatch(migration, /raw_token|password/u);
  assert.match(migration, /token_hash TEXT NOT NULL UNIQUE/u);
});

test("ships no-store and no-referrer policies", () => {
  assert.match(headers, /\/admin\/\*[\s\S]*Cache-Control: private, no-store/u);
  assert.match(headers, /\/owner\/\*[\s\S]*Referrer-Policy: no-referrer/u);
  assert.match(headers, /X-Robots-Tag: noindex, nofollow/u);
  assert.match(headers, /frame-ancestors 'none'/u);
});

test(
  "keeps authentication assets in the private public-mirror allowlist",
  { skip: syncScript === null ? "The public snapshot does not ship the private mirror script." : false },
  () => {
    assert.match(syncScript, /dashboard\/_headers/u);
    assert.match(syncScript, /dashboard\/(?:admin\|owner|\(\?:admin\|owner\))/u);
  }
);
