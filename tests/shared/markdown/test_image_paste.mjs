import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import {
  attachImagePaste,
  createAssetApi,
} from "../../../shared/static/markdown/image-paste.js";

test("paste controller uploads an image and inserts image:// Markdown", async () => {
  const dom = new JSDOM("<textarea>before after</textarea>");
  const textarea = dom.window.document.querySelector("textarea");
  textarea.setSelectionRange(7, 7);
  let inputEvents = 0;
  textarea.addEventListener("input", () => { inputEvents += 1; });
  const file = { name: "diagram.png", type: "image/png" };
  attachImagePaste(textarea, {
    upload: async () => ({ id: 13 }),
  });
  const event = new dom.window.Event("paste", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "clipboardData", {
    value: {
      items: [{
        kind: "file",
        type: "image/png",
        getAsFile: () => file,
      }],
    },
  });

  textarea.dispatchEvent(event);
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(event.defaultPrevented, true);
  assert.equal(textarea.value, "before ![diagram](image://13)after");
  assert.equal(inputEvents, 1);
});

test("asset API sends raw image and owner-only reconcile payload", async () => {
  const calls = [];
  const api = createAssetApi({
    uploadUrl: "/api/assets/images",
    reconcileUrl: "/api/assets/references/reconcile",
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return {
        ok: true,
        status: 200,
        json: async () => ({ id: 3 }),
      };
    },
  });
  const file = { name: "한글 화면.png", type: "image/png" };
  await api.uploadImage(file, "session-1");
  await api.reconcile({ owner_type: "note", owner_id: "7", field: "content" });

  assert.equal(calls[0].url, "/api/assets/images?upload_session_id=session-1");
  assert.equal(calls[0].options.body, file);
  assert.equal(
    calls[0].options.headers["X-File-Name"],
    "%ED%95%9C%EA%B8%80%20%ED%99%94%EB%A9%B4.png",
  );
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    owner_type: "note",
    owner_id: "7",
    field: "content",
  });
  assert.equal(JSON.parse(calls[1].options.body).asset_ids, undefined);
});
