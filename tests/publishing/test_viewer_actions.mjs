import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const template = await readFile(
  new URL("../../publishing/templates/publication_viewer.html", import.meta.url),
  "utf8",
);
const script = await readFile(
  new URL("../../publishing/static/publication-viewer.js", import.meta.url),
  "utf8",
);

test("publication viewer exposes reversible delete semantics", () => {
  assert.match(template, /id="publication-delete"/);
  assert.match(template, /immutable version은 보관/);
  assert.match(template, /\/archive/);
  assert.match(script, /window\.confirm/);
  assert.match(script, /method: "POST"/);
  assert.match(script, /window\.location\.assign/);
  assert.doesNotMatch(script, /https?:\/\//);
});
