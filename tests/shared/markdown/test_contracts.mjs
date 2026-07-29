import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  DEFAULT_RENDER_OPTIONS,
  RENDER_LIMITS,
  assertViewMode,
  normalizeRenderOptions,
} from "../../../shared/static/markdown/contracts.js";
import {
  EDITOR_PROFILES,
  getEditorProfile,
} from "../../../shared/static/markdown/profiles.js";

const golden = JSON.parse(
  await readFile(
    new URL("../../fixtures/markdown/golden.json", import.meta.url),
    "utf8",
  ),
);
const security = JSON.parse(
  await readFile(
    new URL("../../fixtures/markdown/security.json", import.meta.url),
    "utf8",
  ),
);

test("normalizes immutable render options with bounded TOC levels", () => {
  const options = normalizeRenderOptions({ compact: true, minTocLevel: 3 });
  assert.equal(options.compact, true);
  assert.equal(options.breaks, true);
  assert.equal(options.minTocLevel, 3);
  assert.equal(Object.isFrozen(options), true);
  assert.throws(
    () => normalizeRenderOptions({ minTocLevel: 5, maxTocLevel: 2 }),
    /invalid TOC heading range/,
  );
  assert.equal(RENDER_LIMITS.maxSourceLength, 200_000);
  assert.equal(DEFAULT_RENDER_OPTIONS.openExternalLinks, false);
});

test("keeps profiles immutable and limited to UI behavior", () => {
  const full = getEditorProfile("full");
  const compact = getEditorProfile("compact");
  assert.equal(Object.isFrozen(EDITOR_PROFILES), true);
  assert.equal(Object.isFrozen(full.toolbarCommands), true);
  assert.deepEqual(full.viewModes, ["editor", "split", "preview"]);
  assert.deepEqual(compact.viewModes, ["editor", "preview"]);
  assert.equal(full.showToc, true);
  assert.equal(compact.showToc, false);
  assert.equal("sanitizerPolicy" in full, false);
  assert.equal("markdownDialect" in compact, false);
  assert.throws(() => getEditorProfile("calendar"), /unknown editor profile/);
});

test("validates available editor view modes", () => {
  assert.equal(assertViewMode("split"), "split");
  assert.equal(
    assertViewMode("preview", getEditorProfile("compact").viewModes),
    "preview",
  );
  assert.throws(
    () => assertViewMode("split", getEditorProfile("compact").viewModes),
    /unsupported editor view mode/,
  );
});

test("locks versioned golden and security fixtures", () => {
  assert.equal(golden.schema_version, 1);
  assert.equal(security.schema_version, 1);
  assert.ok(golden.cases.some(({ id }) => id === "raw-html-literal"));
  assert.ok(security.cases.some(({ id }) => id === "script-token"));
  assert.ok(security.cases.every(({ markdown }) => markdown.length > 0));
});

