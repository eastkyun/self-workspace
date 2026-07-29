import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { attachMarkdownEditor } from "../../../shared/static/markdown/editor-controller.js";
import { attachSlashPalette } from "../../../shared/static/markdown/slash-palette.js";
import { createMarkdownToolbar } from "../../../shared/static/markdown/toolbar.js";

function environment(value = "") {
  const dom = new JSDOM("<main><textarea aria-label=\"본문\"></textarea></main>", {
    pretendToBeVisual: true,
  });
  const textarea = dom.window.document.querySelector("textarea");
  textarea.value = value;
  textarea.setSelectionRange(value.length, value.length);
  const controller = attachMarkdownEditor(textarea);
  return { dom, textarea, controller };
}

test("toolbar is generated from the profile command registry", () => {
  const { dom } = environment();
  const executed = [];
  const toolbar = createMarkdownToolbar({
    document: dom.window.document,
    profile: "compact",
    execute: (id) => executed.push(id),
  });
  const buttons = [...toolbar.querySelectorAll("button")];
  assert.equal(toolbar.getAttribute("role"), "toolbar");
  assert.ok(buttons.length > 0);
  assert.equal(buttons.some(({ dataset }) => dataset.markdownCommand === "table"), false);
  buttons[0].click();
  assert.deepEqual(executed, [buttons[0].dataset.markdownCommand]);
  assert.ok(buttons.every((button) => button.getAttribute("aria-label")));
});

test("slash palette filters Korean and English aliases and executes by keyboard", () => {
  const { dom, textarea, controller } = environment("/h2");
  const slash = attachSlashPalette(textarea, controller);
  textarea.dispatchEvent(new dom.window.InputEvent("input", { bubbles: true }));
  assert.equal(slash.element.hidden, false);
  assert.equal(textarea.getAttribute("aria-expanded"), "true");
  assert.equal(
    slash.element.querySelector("[role=option]").dataset.markdownCommand,
    "heading2",
  );
  textarea.dispatchEvent(new dom.window.KeyboardEvent("keydown", {
    key: "Enter",
    bubbles: true,
    cancelable: true,
  }));
  assert.equal(textarea.value, "## ");
  assert.equal(slash.element.hidden, true);

  textarea.value = "/표";
  textarea.setSelectionRange(2, 2);
  textarea.dispatchEvent(new dom.window.InputEvent("input", { bubbles: true }));
  assert.equal(
    slash.element.querySelector("[role=option]").dataset.markdownCommand,
    "table",
  );
});

test("palette does not open inside URLs, words, inline code, fences or IME", () => {
  const { dom, textarea, controller } = environment();
  const slash = attachSlashPalette(textarea, controller);
  for (const value of [
    "https://",
    "단어/",
    "`code /",
    "```\n/",
  ]) {
    textarea.value = value;
    textarea.setSelectionRange(value.length, value.length);
    textarea.dispatchEvent(new dom.window.InputEvent("input", { bubbles: true }));
    assert.equal(slash.element.hidden, true, value);
  }

  textarea.value = "/";
  textarea.setSelectionRange(1, 1);
  textarea.dispatchEvent(new dom.window.CompositionEvent("compositionstart", {
    bubbles: true,
  }));
  textarea.dispatchEvent(new dom.window.InputEvent("input", { bubbles: true }));
  assert.equal(slash.element.hidden, true);
});

test("Escape dismisses the current slash query until it changes", () => {
  const { dom, textarea, controller } = environment("/");
  const slash = attachSlashPalette(textarea, controller);
  slash.update();
  assert.equal(slash.element.hidden, false);
  textarea.dispatchEvent(new dom.window.KeyboardEvent("keydown", {
    key: "Escape",
    bubbles: true,
    cancelable: true,
  }));
  assert.equal(slash.element.hidden, true);
  slash.update();
  assert.equal(slash.element.hidden, true);
  textarea.value = "/표";
  textarea.setSelectionRange(2, 2);
  slash.update();
  assert.equal(slash.element.hidden, false);
});

test("destroy removes palette DOM and textarea ARIA state", () => {
  const { textarea, controller } = environment("/");
  const slash = attachSlashPalette(textarea, controller);
  const element = slash.element;
  slash.destroy();
  assert.equal(element.isConnected, false);
  assert.equal(textarea.hasAttribute("aria-controls"), false);
});

