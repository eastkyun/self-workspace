import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  decodePinnedIds,
  defaultPinnedIds,
} from "../../portal/static/hub-state.js";

const template = await readFile(
  new URL("../../portal/templates/home.html", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../portal/static/hub.css", import.meta.url),
  "utf8",
);
const script = await readFile(
  new URL("../../portal/static/hub.js", import.meta.url),
  "utf8",
);

const apps = [
  { id: "calendar", default_pinned: true },
  { id: "docs", default_pinned: false },
];

test("uses registry defaults when no storage exists", () => {
  assert.deepEqual(defaultPinnedIds(apps), ["calendar"]);
  assert.deepEqual(decodePinnedIds(null, apps), ["calendar"]);
});

test("filters deleted and duplicate app IDs", () => {
  assert.deepEqual(
    decodePinnedIds('["docs","removed","docs"]', apps),
    ["docs"],
  );
});

test("recovers from corrupt or non-array storage", () => {
  assert.deepEqual(decodePinnedIds("{broken", apps), ["calendar"]);
  assert.deepEqual(decodePinnedIds('{"calendar":true}', apps), ["calendar"]);
});

test("renders the local quote with semantic responsive markup", () => {
  assert.match(template, /<blockquote class="hub-quote"/);
  assert.match(template, /<cite>\{\{ hub_quote\.author \}\}<\/cite>/);
  assert.match(template, /\{% else %\}[\s\S]*좋은 오후예요/);
  assert.match(styles, /\.hub-quote-text\s*\{[^}]*overflow-wrap:\s*anywhere/s);
  assert.match(styles, /@media \(max-width: 760px\)/);
});

test("signals readiness after the initial hub request settles", () => {
  assert.match(template, /data-workspace-await-ready/);
  assert.match(script, /\.finally\(\(\) => globalThis\.workspaceAppReady\?\.\(\)\)/);
});
