import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  createPublishingApi,
  publicationIdFromTarget,
  publicationTargetValue,
  suggestPublicationSlug,
} from "../../publishing/static/publish-dialog.js";

const template = await readFile(
  new URL("../../apps/notes/app/templates/notes.html", import.meta.url),
  "utf8",
);
const script = await readFile(
  new URL("../../apps/notes/app/static/notes.js", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../apps/notes/app/static/notes.css", import.meta.url),
  "utf8",
);

test("publication UI helpers are deterministic and reject malformed targets", () => {
  assert.equal(suggestPublicationSlug(" Install__Guide 2026 "), "install-guide-2026");
  assert.equal(suggestPublicationSlug("한글 제목"), "");
  assert.equal(suggestPublicationSlug("a".repeat(100)).length, 80);
  assert.equal(publicationTargetValue(13), "publication:13");
  assert.equal(publicationIdFromTarget("publication:13"), 13);
  assert.equal(publicationIdFromTarget("publication:13x"), null);
});

test("publishing client uses explicit workspace endpoints", async () => {
  const calls = [];
  const api = createPublishingApi({
    publicationsUrl: "/api/publications",
    typesUrl: "/api/publication-types",
    fetchImpl: async (url, options = {}) => {
      calls.push({ url, options });
      return { ok: true, status: 200, json: async () => ({ id: 7 }) };
    },
  });

  await api.types();
  await api.listForNote(13);
  await api.preview({ note_id: 13, type_id: "manual" });
  await api.create({ note_id: 13, type_id: "manual", slug: "install" });
  await api.saveDraft(7, { summary: "summary" });
  await api.publish(7);

  assert.deepEqual(calls.map(({ url }) => url), [
    "/api/publication-types",
    "/api/publications?note_id=13",
    "/api/publications/preview-from-note",
    "/api/publications",
    "/api/publications/7/drafts/from-note",
    "/api/publications/7/publish",
  ]);
  assert.equal(calls.at(-1).options.method, "POST");
});

test("saved Note view exposes an accessible publishing workflow", () => {
  assert.match(template, /id="publish-note"/);
  assert.match(template, /<dialog id="publish-dialog"[^>]*aria-labelledby=/);
  assert.match(template, /id="publication-status"[^>]*aria-live="polite"/);
  assert.match(template, /id="publication-target"/);
  assert.match(template, /id="publication-preview-button"/);
  assert.match(template, /id="publication-save-draft"/);
  assert.match(template, /id="publication-publish"/);
  assert.match(script, /state\.creating\s*\|\|\s*state\.editing\s*\|\|\s*isDirty\(\)/);
  assert.match(script, /발행하려면 먼저 메모를 저장하고 읽기 화면으로/);
  assert.match(script, /mountMarkdownViewer/);
  assert.match(script, /publicationPreviewViewer\?\.destroy/);
  assert.match(script, /elements\.publicationTarget\.focus/);
  assert.match(script, /elements\.publishButton\.focus/);
  assert.match(styles, /@media \(max-width: 560px\)/);
  assert.match(styles, /max-height:\s*min\(90dvh/);
});
