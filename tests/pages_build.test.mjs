import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { buildPages } from "../build/pages.mjs";

async function fixture(t) {
  const root = await mkdtemp(join(tmpdir(), "tc-pages-build-"));
  t.after(() => rm(root, { recursive: true, force: true }));
  const files = {
    "index.html": '<title>오늘의 커뮤니티</title><link href="./styles.css" rel="stylesheet"><link rel="icon" href="/favicon.png"><script src="./app.js"></script><script src="/analytics.js" data-track="false"></script>',
    "privacy.html": '<link href="/styles.css" rel="stylesheet"><script src="/analytics.js" data-standalone data-track="false"></script>',
    "admin/index.html": '<link href="/admin/styles.css" rel="stylesheet"><script src="./admin.js"></script>',
    "app.js": 'document.title = "오늘의 커뮤니티";',
    "analytics.js": 'window.analyticsFixture = true;',
    "styles.css": 'body { color: black; }',
    "admin/admin.js": 'window.adminFixture = true;',
    "admin/styles.css": 'body { color: navy; }',
    "favicon.png": Buffer.from([137, 80, 78, 71]),
    "_routes.json": '{"version":1,"include":["/","/api/*","/sitemap.xml"],"exclude":[]}',
    "_headers": '/admin/*\n  Cache-Control: private, no-store\n',
    "sitemap.xml": '<urlset/>',
    "sitemap.txt": 'https://todaycommunity.pages.dev/',
    "robots.txt": 'User-agent: *\nAllow: /\n',
  };
  await mkdir(join(root, "dashboard", "admin"), { recursive: true });
  for (const [name, content] of Object.entries(files)) await writeFile(join(root, "dashboard", name), content);
  return { root, files };
}

test("Pages build fingerprints local scripts and styles without changing metadata, attributes, security headers or icon URLs", async t => {
  const { root, files } = await fixture(t);
  const { output, assets } = await buildPages(root);
  for (const [original, fingerprinted] of Object.entries(assets)) {
    const bytes = await readFile(join(output, fingerprinted));
    const hash = createHash("sha256").update(bytes).digest("hex").slice(0, 16);
    assert.ok(fingerprinted.includes(`.${hash}.`));
    assert.deepEqual(bytes, await readFile(join(root, "dashboard", original)));
    assert.deepEqual(await readFile(join(output, original)), bytes, "stable URLs remain available");
  }
  const html = await readFile(join(output, "index.html"), "utf8");
  assert.ok(html.includes(`src="${assets['/app.js']}"`));
  assert.ok(html.includes(`href="${assets['/styles.css']}"`));
  assert.ok(html.includes(`src="${assets['/analytics.js']}" data-track="false"`));
  assert.match(html, /<title>오늘의 커뮤니티<\/title>/);
  assert.match(html, /href="\/favicon.png"/);
  assert.ok((await readFile(join(output, "privacy.html"), "utf8")).includes(`href="${assets['/styles.css']}"`));
  assert.ok((await readFile(join(output, "admin/index.html"), "utf8")).includes(`src="${assets['/admin/admin.js']}"`));
  for (const name of ["_headers", "_routes.json", "robots.txt", "sitemap.xml", "sitemap.txt"]) {
    assert.equal(await readFile(join(output, name), "utf8"), files[name]);
  }
  for (const [name, content] of Object.entries(files)) {
    assert.deepEqual(await readFile(join(root, "dashboard", name)), Buffer.from(content), "source files stay unchanged");
  }
});

test("unchanged assets keep their URLs, changed content gets a new URL, and stale build aliases are removed", async t => {
  const { root } = await fixture(t);
  const first = await buildPages(root);
  const second = await buildPages(root);
  assert.deepEqual(second.assets, first.assets);
  await writeFile(join(root, "dashboard", "app.js"), 'document.title = "오늘의 커뮤니티"; window.newVersion = true;');
  const third = await buildPages(root);
  assert.notEqual(third.assets["/app.js"], first.assets["/app.js"]);
  assert.equal(third.assets["/styles.css"], first.assets["/styles.css"]);
  assert.ok(!(await readdir(third.output)).includes(first.assets["/app.js"].slice(1)));
  assert.ok((await readFile(join(third.output, "index.html"), "utf8")).includes(third.assets["/app.js"]));
});

test("missing local JS or CSS prevents a deployable build", async t => {
  const { root } = await fixture(t);
  await writeFile(join(root, "dashboard", "index.html"), '<script src="/missing.js"></script>');
  await assert.rejects(buildPages(root), /Missing local asset/);
});
