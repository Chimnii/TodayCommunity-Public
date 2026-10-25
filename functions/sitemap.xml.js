// Preserve the static sitemap response while making its requests observable
// through a bounded Pages Functions tail during crawl diagnostics.
export function onRequest(context) {
  return context.env.ASSETS.fetch(context.request);
}
