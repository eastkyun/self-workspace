import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import {JSDOM} from "jsdom";
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

test("shared shell persists sidebar workspace selection before navigation", () => {
  const shellSource = fs.readFileSync("shared/static/shell.js", "utf8");
  assert.match(shellSource, /workspaceList\?\.addEventListener\("click"/);
  assert.match(shellSource, /workspaces\/\$\{encodeURIComponent\(link\.dataset\.workspaceId\)\}\/select/);
  assert.match(shellSource, /select`, \{\s*method: "POST",\s*headers: \{"X-CSRF-Token": csrfToken\(\)\}/);
  assert.match(shellSource, /window\.location\.assign\(link\.href\)/);
  assert.doesNotMatch(shellSource, /workspace selection failed[\s\S]{0,100}location\.reload/);
});

test("shared shell keeps canonical app links on the workspace in the current route", () => {
  const shellSource = fs.readFileSync("shared/static/shell.js", "utf8");
  assert.match(shellSource, /body\.dataset\.workspaceId \|\| user\.last_workspace_id/);
  assert.match(shellSource, /rewriteWorkspaceAppLinks\(activeWorkspaceId\)/);
});

test("shared shell selects once and keeps app links on the route workspace", async () => {
  const dom = new JSDOM(`<!doctype html><body data-workspace-id="workspace-b" data-session-cookie="my_workspace_session">
    <aside id="sidebar"><div data-workspace-list></div><p data-workspace-status></p></aside>
    <main id="workspace-main"></main>
    <div id="workspace-guest-actions" hidden></div>
    <div id="workspace-account" hidden><button id="workspace-account-toggle"></button><div id="workspace-account-menu" hidden></div><span id="workspace-account-label"></span><span id="workspace-account-avatar"></span><span id="workspace-account-name"></span><span id="workspace-account-email"></span></div>
    <a data-app-id="notes"></a><a data-app-id="calendar"></a><a data-app-id="publishing"></a>
  </body>`, {url: "http://testserver/w/workspace-b/home"});
  const requests = [];
  const fetchStub = async (input, options = {}) => {
    const url = String(input);
    requests.push({url, options});
    if (url.endsWith("api/v1/workspaces")) return {ok: true, json: async () => [{id: "workspace-b", name: "B"}]};
    if (url.endsWith("api/v1/invitations")) return {ok: true, json: async () => []};
    if (url.endsWith("api/v1/users/me")) return {ok: true, json: async () => ({id: "user", email: "user@example.com", display_name: "User", last_workspace_id: "workspace-a"})};
    if (url === "/api/v1/workspaces/workspace-b/select") return {ok: true};
    if (url === "/api/v1/workspaces/unavailable/select") return {ok: false};
    throw new Error(`unexpected request: ${url}`);
  };
  Object.assign(globalThis, {
    window: dom.window,
    document: dom.window.document,
    localStorage: dom.window.localStorage,
    CustomEvent: dom.window.CustomEvent,
    fetch: fetchStub,
    ResizeObserver: class { observe() {} },
  });
  dom.window.matchMedia = () => ({matches: false, addEventListener() {}});
  dom.window.requestAnimationFrame = (callback) => callback();
  dom.window.document.cookie = "my_workspace_session_csrf=csrf-token";
  const browserErrors = [];
  dom.window.addEventListener("error", (event) => browserErrors.push(event.error?.stack || event.message));

  await import(`../../shared/static/shell.js?workspace-selection-test=${Date.now()}`);
  await globalThis.workspaceAccountPromise;
  await new Promise((resolve) => setTimeout(resolve, 0));

  for (const appId of ["notes", "calendar", "publishing"]) {
    assert.match(dom.window.document.querySelector(`[data-app-id="${appId}"]`).getAttribute("href"), /\/w\/workspace-b\//);
  }
  const workspaceLink = dom.window.document.querySelector("[data-workspace-list] [data-workspace-id='workspace-b']");
  workspaceLink.href = "#selected";
  workspaceLink.dispatchEvent(new dom.window.MouseEvent("click", {bubbles: true, cancelable: true, button: 0}));
  await new Promise((resolve) => setTimeout(resolve, 10));

  const selectionRequests = requests.filter(({url}) => url.endsWith("/select"));
  assert.equal(selectionRequests.length, 1, JSON.stringify({requests, browserErrors}));
  assert.equal(selectionRequests[0].options.method, "POST");
  assert.equal(selectionRequests[0].options.headers["X-CSRF-Token"], "csrf-token");
  assert.equal(dom.window.location.hash, "#selected");

  workspaceLink.dataset.workspaceId = "unavailable";
  workspaceLink.href = "#failed";
  workspaceLink.dispatchEvent(new dom.window.MouseEvent("click", {bubbles: true, cancelable: true, button: 0}));
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(dom.window.location.hash, "#selected");
  assert.match(dom.window.document.querySelector("[data-workspace-status]").textContent, /전환하지 못했습니다/);
});
