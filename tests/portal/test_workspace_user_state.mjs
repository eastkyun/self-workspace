import assert from "node:assert/strict";
import test from "node:test";

import { createWorkspaceUserState } from "../../shared/static/workspace-user-state.js";

test("workspace user state encodes resource keys and sends CSRF on mutations", async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({url, options});
    return {ok: true, status: options.method ? 204 : 200, json: async () => []};
  };
  try {
    const state = createWorkspaceUserState({baseUrl: "/api/v1/workspaces/w", csrfToken: () => "csrf"});
    await state.favorite("calendar", "a/b", {app_key: "calendar", label: "untrusted", href: "https://evil.test"});
    await state.recent("note", "n 1", {app_key: "notes", label: "Note", href: "/w/w/notes"});
    await state.unfavorite("note", "n 1");
    assert.equal(calls[0].url, "/api/v1/workspaces/w/favorites/calendar/a%2Fb");
    assert.equal(calls[0].options.headers["X-CSRF-Token"], "csrf");
    assert.deepEqual(JSON.parse(calls[0].options.body), {app_key: "calendar"});
    assert.equal(calls[1].url, "/api/v1/workspaces/w/recent-views");
    assert.equal(calls[1].options.method, "POST");
    assert.deepEqual(JSON.parse(calls[1].options.body), {resource_type: "note", resource_id: "n 1", app_key: "notes"});
    assert.equal(calls[2].options.method, "DELETE");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
