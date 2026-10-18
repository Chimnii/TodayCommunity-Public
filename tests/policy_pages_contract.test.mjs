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

test("discloses guest-only advertising, cookieless analytics and third-party data handling", () => {
  assert.match(privacyHtml, /공개 글 목록을 회원가입 없이 제공합니다/u);
  assert.match(privacyHtml, /비로그인 방문자에게 Adcash의 일반 배너/u);
  assert.match(privacyHtml, /인증 상태를 확인하지 못한 경우에도 광고를 불러오지 않습니다/u);
  assert.match(privacyHtml, /쿠키·웹 비콘/u);
  assert.match(privacyHtml, /IP 주소, 브라우저·기기 정보/u);
  assert.match(privacyHtml, /href="https:\/\/adcash.com\/legal\/"/u);
  assert.match(privacyHtml, /Cloudflare Web Analytics를 사용합니다/u);
  assert.match(privacyHtml, /페이지 조회, 유입 사이트, 국가, 브라우저·기기 유형과 페이지 성능/u);
  assert.match(privacyHtml, /개인을 추적하는 쿠키나 브라우저 지문을 사용하지 않습니다/u);
  assert.match(privacyHtml, /통계만으로 특정 방문자의 신원이나 실제 사람 여부를 확정할 수는 없습니다/u);
  assert.match(privacyHtml, /href="https:\/\/developers\.cloudflare\.com\/web-analytics\/about\/"/u);
  assert.match(privacyHtml, /href="https:\/\/www\.cloudflare\.com\/privacypolicy\/"/u);
  assert.doesNotMatch(privacyHtml, /현재 광고는 게재하지 않습니다|이용자의 개인정보를 직접 수집하거나\s+저장하지 않습니다|시크릿 링크|관리자|기사 평가/u);
  assert.match(privacyHtml, /시행일: 2026년 10월 19일/u);
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
