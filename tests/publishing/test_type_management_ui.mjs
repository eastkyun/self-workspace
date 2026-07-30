import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const template = await readFile(
  new URL("../../publishing/templates/publication_types.html", import.meta.url),
  "utf8",
);
const script = await readFile(
  new URL("../../publishing/static/publication-types.js", import.meta.url),
  "utf8",
);

test("publication type management template is accessible and shell-integrated", () => {
  assert.match(template, /extends "app_shell\.html"/);
  assert.match(template, /aria-labelledby="publication-types-title"/);
  assert.match(template, /role="status"/);
  assert.match(template, /type="submit"/);
  assert.match(template, /pattern="\[a-z\]\[a-z0-9-\]\*"/);
  assert.match(template, /\/static\/publishing\/publication-types\.js/);
});

test("management UI uses the declarative API without remote requests", () => {
  assert.match(script, /dataApiEndpoint|dataset\.apiEndpoint/);
  assert.match(script, /method: editingId \? "PUT" : "POST"/);
  assert.match(script, /method: "DELETE"/);
  assert.match(script, /JSON\.stringify\(payload\)/);
  assert.match(script, /Workspace를 재시작/);
  assert.doesNotMatch(script, /https?:\/\//);
});
