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

const [publicHtml, aboutHtml, privacyHtml, adsTxt, robots, sitemap, syncScript] =
  await Promise.all([
    readFile(new URL("../dashboard/index.html", import.meta.url), "utf8"),
    readFile(new URL("../dashboard/about.html", import.meta.url), "utf8"),
    readFile(new URL("../dashboard/privacy.html", import.meta.url), "utf8"),
    readFile(new URL("../dashboard/ads.txt", import.meta.url), "utf8"),
    readFile(new URL("../dashboard/robots.txt", import.meta.url), "utf8"),
    readFile(new URL("../dashboard/sitemap.xml", import.meta.url), "utf8"),
    readOptionalFile(new URL("../scripts/sync_public_mirror.ps1", import.meta.url)),
  ]);

test("declares one canonical URL for each public page", () => {
  assert.match(
    publicHtml,
    /<link rel="canonical" href="https:\/\/todaycommunity\.pages\.dev\/" \/>/u,
  );
  assert.match(
    aboutHtml,
    /<link rel="canonical" href="https:\/\/todaycommunity\.pages\.dev\/about" \/>/u,
  );
  assert.match(
    privacyHtml,
    /<link rel="canonical" href="https:\/\/todaycommunity\.pages\.dev\/privacy" \/>/u,
  );
});

test("describes the public root as the TodayCommunity website", () => {
  const structuredDataMatch = publicHtml.match(
    /<script type="application\/ld\+json">\s*([\s\S]*?)\s*<\/script>/u,
  );
  assert.ok(structuredDataMatch);

  const structuredData = JSON.parse(structuredDataMatch[1]);
  assert.equal(structuredData["@context"], "https://schema.org");
  assert.equal(structuredData["@type"], "WebSite");
  assert.equal(structuredData.name, "오늘의 커뮤니티");
  assert.equal(structuredData.alternateName, "TodayCommunity");
  assert.equal(structuredData.url, "https://todaycommunity.pages.dev/");
});

test("publishes AdSense ownership data without loading the ad runtime", () => {
  assert.match(
    publicHtml,
    /<meta name="google-adsense-account" content="ca-pub-2749794433076203" \/>/u,
  );
  assert.doesNotMatch(publicHtml, /pagead2\.googlesyndication\.com|adsbygoogle/u);
  assert.equal(
    adsTxt.trim(),
    "google.com, pub-2749794433076203, DIRECT, f08c47fec0942fa0",
  );
});

test("allows public crawling and advertises the sitemap", () => {
  assert.match(robots, /^User-agent: \*\r?$/mu);
  assert.match(robots, /^Allow: \/\r?$/mu);
  assert.doesNotMatch(robots, /^Disallow: \/\r?$/mu);
  assert.match(
    robots,
    /^Sitemap: https:\/\/todaycommunity\.pages\.dev\/sitemap\.xml\r?$/mu,
  );
});

test("lists only the three canonical public pages in the sitemap", () => {
  const locations = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/gu)].map((match) => match[1]);
  assert.deepEqual(locations, [
    "https://todaycommunity.pages.dev/",
    "https://todaycommunity.pages.dev/about",
    "https://todaycommunity.pages.dev/privacy",
  ]);
  for (const location of locations) {
    assert.doesNotMatch(location, /[?&]|\/admin|\/owner|\/api/u);
  }
});

test("exports search discovery files through the public mirror", () => {
  if (syncScript !== null) {
    assert.match(syncScript, /"dashboard\/ads\.txt"/u);
    assert.match(syncScript, /"dashboard\/robots\.txt"/u);
    assert.match(syncScript, /"dashboard\/sitemap\.xml"/u);
  }
});
