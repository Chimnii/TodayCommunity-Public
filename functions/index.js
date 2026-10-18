import { onRequestGet as loadArchive } from "./api/archive.js";
import { renderPublicPage } from "./_public-page.js";

export function onRequestGet(context) {
  return renderPublicPage(context, loadArchive);
}

export function onRequestHead(context) {
  return renderPublicPage(context, loadArchive);
}
