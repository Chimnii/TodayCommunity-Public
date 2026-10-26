import { createHash } from "node:crypto";
import { cp, lstat, mkdir, readFile, readdir, realpath, rm, writeFile } from "node:fs/promises";
import { join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

async function filesUnder(directory, prefix = "") {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const name = prefix + entry.name;
    if (entry.isSymbolicLink()) throw new Error(`Asset symlinks are not supported: ${name}`);
    if (entry.isDirectory()) files.push(...await filesUnder(join(directory, entry.name), `${name}/`));
    else if (entry.isFile()) files.push(name);
  }
  return files.sort();
}

// Only the generated .wrangler/pages-assets directory is disposable. Keep the
// source tree and stable asset URLs for existing links and local development.
export async function buildPages(repositoryRoot = fileURLToPath(new URL("../", import.meta.url))) {
  const root = await realpath(repositoryRoot);
  const source = join(root, "dashboard");
  const staging = join(root, ".wrangler");
  await mkdir(staging, { recursive: true });
  if (await realpath(staging) !== staging) throw new Error("Refusing a redirected build directory");
  const output = resolve(staging, "pages-assets");
  if (relative(root, output) !== [".wrangler", "pages-assets"].join(sep)) {
    throw new Error("Build output must stay inside the repository's .wrangler directory");
  }
  const existing = await lstat(output).catch(error => { if (error.code !== "ENOENT") throw error; });
  if (existing && (!existing.isDirectory() || existing.isSymbolicLink())) {
    throw new Error("Refusing to replace a redirected or non-directory build output");
  }
  const files = await filesUnder(source);
  await rm(output, { recursive: true, force: true });
  await cp(source, output, { recursive: true });

  const assets = new Map();
  for (const file of files.filter(file => /\.(?:js|css)$/.test(file))) {
    const bytes = await readFile(join(source, file));
    const hash = createHash("sha256").update(bytes).digest("hex").slice(0, 16);
    const fingerprinted = file.replace(/\.(js|css)$/, `.${hash}.$1`);
    await writeFile(join(output, fingerprinted), bytes);
    assets.set(`/${file}`, `/${fingerprinted}`);
  }
  for (const file of files.filter(file => file.endsWith(".html"))) {
    const html = await readFile(join(source, file), "utf8");
    const rewritten = html.replace(/\b(src|href)=(['"])([^'"<>]+)\2/g, (attribute, name, quote, value) => {
      const url = new URL(value, `https://build.invalid/${file}`);
      if (url.origin !== "https://build.invalid" || !/\.(?:js|css)$/.test(url.pathname)) return attribute;
      const asset = assets.get(url.pathname);
      if (!asset) throw new Error(`Missing local asset in ${file}: ${value}`);
      return `${name}=${quote}${asset}${url.search}${url.hash}${quote}`;
    });
    await writeFile(join(output, file), rewritten);
  }
  return { output, assets: Object.fromEntries(assets) };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const result = await buildPages();
  console.log(`Built Pages assets in ${result.output} (${Object.keys(result.assets).length} fingerprinted JS/CSS files)`);
}
