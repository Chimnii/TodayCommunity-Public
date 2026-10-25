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

const [publicHtml, aboutHtml, privacyHtml, adsTxt, robots, sitemap, textSitemap, syncScript] =
  await Promise.all([
    readFile(new URL("../dashboard/index.html", import.meta.url), "utf8"),
    readFile(new URL("../dashboard/about.html", import.meta.url), "utf8"),
    readFile(new URL("../dashboard/privacy.html", import.meta.url), "utf8"),
    readFile(new URL("../dashboard/ads.txt", import.meta.url), "utf8"),
    readFile(new URL("../dashboard/robots.txt", import.meta.url), "utf8"),
    readFile(new URL("../dashboard/sitemap.xml", import.meta.url), "utf8"),
    readFile(new URL("../dashboard/sitemap.txt", import.meta.url), "utf8"),
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
  assert.deepEqual(structuredData.alternateName, ["오늘의커뮤니티", "TodayCommunity"]);
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

test("lists canonical public archives and information pages without private or filter URLs", () => {
  const locations = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/gu)].map((match) => match[1]);
  assert.deepEqual(locations, [
    "https://todaycommunity.pages.dev/",
    "https://todaycommunity.pages.dev/?target=dcinside-agent-stack",
    "https://todaycommunity.pages.dev/?target=dcinside-zeus-pride",
    "https://todaycommunity.pages.dev/?target=fmkorea-munich",
    "https://todaycommunity.pages.dev/?target=game-news",
    "https://todaycommunity.pages.dev/?target=all",
    "https://todaycommunity.pages.dev/about",
    "https://todaycommunity.pages.dev/privacy",
  ]);
  for (const location of locations) {
    assert.doesNotMatch(location, /[&]|favorites|\/admin|\/owner|\/api|cursor=|search=/u);
  }
});

test("keeps the alternative text sitemap identical to the XML canonical URL set", () => {
  const xmlLocations = [...sitemap.matchAll(/<loc>([^<]+)<\/loc>/gu)].map((match) => match[1]);
  assert.deepEqual(textSitemap.trimEnd().split(/\r?\n/u), xmlLocations);
  assert.equal(new Set(xmlLocations).size, xmlLocations.length);
  assert.doesNotMatch(textSitemap, /\uFEFF|[<>]/u);
});

test("exports search discovery files through the public mirror", () => {
  if (syncScript !== null) {
    assert.match(syncScript, /"dashboard\/ads\.txt"/u);
    assert.match(syncScript, /"dashboard\/robots\.txt"/u);
    assert.match(syncScript, /"dashboard\/sitemap\.xml"/u);
    assert.equal([...syncScript.matchAll(/"dashboard\/sitemap\.txt"/gu)].length, 2);
    assert.match(syncScript, /"dashboard\/favicon\.ico"/u);
    assert.match(syncScript, /"dashboard\/404\.html"/u);
  }
});

test("default favicon is an ICO containing the approved PNG, not an HTML fallback", async () => {
  const icon = await readFile(new URL("../dashboard/favicon.ico", import.meta.url));
  const png = await readFile(new URL("../dashboard/favicon.png", import.meta.url));
  assert.equal(icon.readUInt16LE(0), 0);
  assert.equal(icon.readUInt16LE(2), 1);
  assert.equal(icon.readUInt16LE(4), 1);
  assert.equal(icon[6], png.readUInt32BE(16));
  assert.equal(icon[7], png.readUInt32BE(20));
  const size = icon.readUInt32LE(14);
  const offset = icon.readUInt32LE(18);
  assert.deepEqual(icon.subarray(offset, offset + size), png);
});
