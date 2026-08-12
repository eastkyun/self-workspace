import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { workspaceAppRoute } from "../../portal/static/hub-state.js";
import { migrateLegacyPins } from "../../shared/static/workspace-apps.js";

const source = fs.readFileSync("shared/static/workspace-settings.js", "utf8");
const appsSource = fs.readFileSync("shared/static/workspace-apps.js", "utf8");

test("workspace invitation UI distinguishes delivery success from failure", () => {
  assert.match(source, /delivery_status === "SENT"/);
  assert.match(source, /초대를 전송했습니다\./);
  assert.match(source, /초대가 생성되었지만 전송하지 못했습니다/);
  assert.match(source, /aria-live|data-status/);
});

test("workspace invitation submit preserves stable hooks and busy feedback", () => {
  assert.match(source, /\[data-invitation-form\]/);
  assert.match(source, /form\.dataset\.workspaceId/);
  assert.match(source, /button\.disabled = true/);
  assert.match(source, /aria-busy|초대 중/);
  assert.match(source, /finally/);
});

test("permission override restores its previous value after a failed request", () => {
  assert.match(source, /\[data-permission-matrix\]/);
  assert.match(source, /select\.dataset\.previous/);
  assert.match(source, /select\.value = previous/);
  assert.match(source, /select\.disabled = true/);
  assert.match(source, /finally/);
});

test("workspace app settings are scoped to their page root", () => {
  assert.match(appsSource, /\[data-workspace-app-settings\]/);
  assert.match(appsSource, /if \(!root\) return/);
  assert.match(appsSource, /#workspace-app-list/);
  assert.match(appsSource, /#app-settings-status/);
});

test("workspace app toggle exposes busy and failure feedback without false state", () => {
  assert.match(appsSource, /button\.disabled = true/);
  assert.match(appsSource, /button\.disabled = false/);
  assert.match(appsSource, /앱 설정을 변경하지 못했습니다/);
  assert.doesNotMatch(appsSource, /dataset\.enabled\s*=\s*enabled/);
});

test("legacy pins import only for the eligible personal workspace and preserve calendar", () => {
  const legacyKey = "my-workspace:pinned-apps:v1";
  const values = new Map([[legacyKey, JSON.stringify(["calendar", "notes"])]]);
  const storage = {
    getItem(key) {
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      values.set(key, value);
    },
  };
  migrateLegacyPins(storage, false, "u", "other");
  assert.equal(values.has("my-workspace:pinned-apps:v2:u:other"), false);
  migrateLegacyPins(storage, true, "u", "personal");
  const copied = JSON.parse(values.get("my-workspace:pinned-apps:v2:u:personal"));
  assert.deepEqual(copied, ["calendar", "notes"]);
  assert.equal(values.get(legacyKey), JSON.stringify(["calendar", "notes"]));
  values.set("my-workspace:pinned-apps:v2:u:existing", "[\"notes\"]");
  migrateLegacyPins(storage, true, "u", "existing");
  assert.equal(values.get("my-workspace:pinned-apps:v2:u:existing"), "[\"notes\"]");
  const denied = {
    getItem() {
      throw new Error("denied");
    },
    setItem() {
      throw new Error("denied");
    },
  };
  assert.doesNotThrow(() => migrateLegacyPins(denied, true, "u", "denied"));
  values.set(legacyKey, "not json");
  assert.doesNotThrow(() => migrateLegacyPins(storage, true, "u", "malformed"));
  assert.equal(values.has("my-workspace:pinned-apps:v2:u:malformed"), false);
});

test("hub routes publishing through the selected workspace", () => {
  assert.equal(
    workspaceAppRoute({id: "publishing", route: "/apps/publishing/"}, "workspace-id"),
    "/w/workspace-id/publications",
  );
});

test("shared shell rewrites every tenant app including publishing", () => {
  const shellSource = fs.readFileSync("shared/static/shell.js", "utf8");
  assert.match(shellSource, /\["calendar", "notes", "publishing"\]/);
});
