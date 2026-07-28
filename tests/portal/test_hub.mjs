import assert from "node:assert/strict";
import test from "node:test";

import {
  decodePinnedIds,
  defaultPinnedIds,
} from "../../portal/static/hub-state.js";

const apps = [
  { id: "calendar", defaultPinned: true },
  { id: "docs", defaultPinned: false },
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
