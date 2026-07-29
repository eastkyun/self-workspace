import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { attachMarkdownEditor } from "../../../shared/static/markdown/editor-controller.js";

function createTextarea(value = "") {
  const dom = new JSDOM("<textarea aria-label=\"본문\"></textarea>", {
    pretendToBeVisual: true,
  });
  const textarea = dom.window.document.querySelector("textarea");
  textarea.value = value;
  return { dom, textarea };
}

test("controller executes commands and publishes the resulting source", () => {
  const { textarea } = createTextarea("한글");
  const changes = [];
  const controller = attachMarkdownEditor(textarea, {
    onChange: (value) => changes.push(value),
  });
  textarea.setSelectionRange(0, 2);
  controller.execute("bold");

  assert.equal(textarea.value, "**한글**");
  assert.deepEqual(controller.getSelection(), { start: 2, end: 4 });
  assert.deepEqual(changes, ["**한글**"]);
});

test("keyboard shortcuts use Ctrl and Meta without firing during IME", () => {
  const { dom, textarea } = createTextarea("문서");
  const controller = attachMarkdownEditor(textarea);
  textarea.setSelectionRange(0, 2);
  textarea.dispatchEvent(new dom.window.KeyboardEvent("keydown", {
    key: "b",
    ctrlKey: true,
    bubbles: true,
  }));
  assert.equal(textarea.value, "**문서**");

  textarea.value = "한글";
  textarea.setSelectionRange(0, 2);
  textarea.dispatchEvent(new dom.window.CompositionEvent("compositionstart", {
    bubbles: true,
  }));
  textarea.dispatchEvent(new dom.window.KeyboardEvent("keydown", {
    key: "i",
    ctrlKey: true,
    bubbles: true,
  }));
  assert.equal(textarea.value, "한글");
  assert.equal(controller.isComposing(), true);
  textarea.dispatchEvent(new dom.window.CompositionEvent("compositionend", {
    bubbles: true,
  }));
  assert.equal(controller.isComposing(), false);
});

test("profiles constrain commands without changing Markdown behavior", () => {
  const { textarea } = createTextarea("문서");
  const controller = attachMarkdownEditor(textarea, { profile: "minimal" });
  assert.throws(() => controller.execute("heading1"), /unavailable/);
  textarea.setSelectionRange(2, 2);
  controller.execute("bold");
  assert.equal(textarea.value, "문서****");
});

test("preview command delegates without editing the textarea", () => {
  const { textarea } = createTextarea("문서");
  let toggles = 0;
  const controller = attachMarkdownEditor(textarea, {
    onPreviewToggle: () => {
      toggles += 1;
    },
  });
  assert.equal(controller.execute("preview-toggle"), null);
  assert.equal(textarea.value, "문서");
  assert.equal(toggles, 1);
});

test("destroy removes listeners and permits a clean remount", () => {
  const { dom, textarea } = createTextarea("문서");
  let changes = 0;
  const first = attachMarkdownEditor(textarea, {
    onChange: () => {
      changes += 1;
    },
  });
  assert.throws(() => attachMarkdownEditor(textarea), /already attached/);
  first.destroy();

  textarea.dispatchEvent(new dom.window.InputEvent("input", { bubbles: true }));
  assert.equal(changes, 0);
  const second = attachMarkdownEditor(textarea);
  second.destroy();
  assert.throws(() => second.getValue(), /destroyed/);
});
