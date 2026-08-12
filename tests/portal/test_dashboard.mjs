import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";

const homeTemplate = await readFile(
  new URL("../../identity/templates/workspace_home.html", import.meta.url),
  "utf8",
);
const homeScript = await readFile(
  new URL("../../shared/static/workspace-home.js", import.meta.url),
  "utf8",
);
const searchScript = await readFile(
  new URL("../../shared/static/workspace-search.js", import.meta.url),
  "utf8",
);

test("dashboard markup exposes accessible layout controls and partial failure state", () => {
  assert.match(homeTemplate, /data-dashboard-dialog/);
  assert.match(homeTemplate, /aria-live="polite"/);
  assert.match(homeTemplate, /widget\.error/);
  assert.match(homeTemplate, /다른 위젯은 계속 사용할 수 있습니다/);
});

test("dashboard mutations are workspace scoped and CSRF protected", () => {
  assert.match(homeScript, /encodeURIComponent\(dashboard\.dataset\.workspaceId\)/);
  assert.match(homeScript, /X-CSRF-Token/);
  assert.match(homeScript, /dashboard\/layout/);
  assert.match(homeScript, /pointerdown/);
  assert.match(homeScript, /dragstart/);
  assert.match(homeScript, /data-widget-drag-handle/);
  assert.match(homeScript, /pointermove/);
  assert.match(homeScript, /data-widget-resize/);
});

test("workspace search renders provider text without HTML injection", () => {
  assert.match(searchScript, /createTextNode/);
  assert.doesNotMatch(searchScript, /innerHTML/);
  assert.match(searchScript, /encodeURIComponent\(workspaceId\)/);
  assert.match(searchScript, /payload\.failures/);
});
