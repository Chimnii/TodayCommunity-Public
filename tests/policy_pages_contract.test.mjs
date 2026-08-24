import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function readOptionalFile(url) {
  try {
    return await readFile(url, "utf8");
  } catch (error) {
    if (error?.code === "ENOENT") {
      return null;
    }
    throw error;
  }
}

const [publicHtml, aboutHtml, privacyHtml, css, syncScript] = await Promise.all([
  readFile(new URL("../dashboard/index.html", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/about.html", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/privacy.html", import.meta.url), "utf8"),
  readFile(new URL("../dashboard/styles.css", import.meta.url), "utf8"),
  readOptionalFile(new URL("../scripts/sync_public_mirror.ps1", import.meta.url)),
]);

test("links the two public policy pages from every public surface", () => {
  for (const html of [publicHtml, aboutHtml, privacyHtml]) {
    assert.match(html, /href="\/about"/u);
    assert.match(html, /href="\/privacy"/u);
    assert.match(html, /<nav aria-label="사이트 안내">/u);
  }

  assert.match(aboutHtml, /href="\/about" aria-current="page"/u);
  assert.match(privacyHtml, /href="\/privacy" aria-current="page"/u);
});

test("describes the service as a metadata-only bookmark that requires the original page", () => {
  assert.match(aboutHtml, /화제가 된 글을 한곳에서 편하게\s+찾아보기 위한 페이지/u);
  assert.match(aboutHtml, /글의 제목과 원문 링크 등 목록에 필요한 정보만 표시합니다/u);
  assert.match(aboutHtml, /원문의 본문과 이미지는\s+가져오거나 저장하지 않으며/u);
  assert.match(aboutHtml, /글을 읽으려면 원문 페이지로 이동해야 합니다/u);
  assert.doesNotMatch(aboutHtml, /개인 운영|제휴|공식적으로 운영|삭제·정정|GitHub/u);
});

test("states that the public page collects no personal data and keeps advertising conditional", () => {
  assert.match(privacyHtml, /이용자의 개인정보를 직접 수집하거나\s+저장하지 않습니다/u);
  assert.match(privacyHtml, /현재 광고는 게재하지 않습니다/u);
  assert.match(privacyHtml, /광고가 게재되는 경우에는 광고 제공 과정에서\s+쿠키 등이 사용될 수 있으며/u);
  assert.match(privacyHtml, /광고가 게재되지 않는 경우에는 해당하지 않습니다/u);
  assert.match(privacyHtml, /광고를 도입할 때 실제 처리 내용을 이 방침에 반영합니다/u);
  assert.doesNotMatch(privacyHtml, /시크릿 링크|관리자|IP 주소|기사 평가|Google|adssettings|partner-sites|GitHub/u);
  assert.match(privacyHtml, /시행일: 2026년 8월 25일/u);
});

test("ships static, responsive pages through the public mirror", () => {
  for (const html of [aboutHtml, privacyHtml]) {
    assert.match(html, /<link rel="stylesheet" href="\/styles\.css" \/>/u);
    assert.match(html, /class="skip-link" href="#main-content"/u);
    assert.doesNotMatch(html, /<script\b/iu);
  }

  assert.match(css, /\.policy-content\s*\{/u);
  assert.match(css, /@media \(max-width:\s*520px\)[\s\S]*\.policy-content\s*\{/u);
  if (syncScript !== null) {
    assert.match(syncScript, /"dashboard\/about\.html"/u);
    assert.match(syncScript, /"dashboard\/privacy\.html"/u);
  }
});
