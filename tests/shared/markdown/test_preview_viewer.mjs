import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { attachMarkdownPreview } from "../../../shared/static/markdown/preview.js";
import { createMarkdownRenderer } from "../../../shared/static/markdown/renderer.js";
import { mountMarkdownViewer } from "../../../shared/static/markdown/viewer.js";

function environment() {
  const dom = new JSDOM([
    "<div data-markdown-editor>",
    "<div class=\"markdown-editor__workspace\">",
    "<textarea></textarea><div id=\"preview\"></div>",
    "</div></div>",
  ].join(""), { pretendToBeVisual: true });
  const document = dom.window.document;
  return {
    dom,
    textarea: document.querySelector("textarea"),
    preview: document.querySelector("#preview"),
    renderer: createMarkdownRenderer({ window: dom.window }),
  };
}

test("viewer uses renderer metadata to build an accessible TOC", () => {
  const { dom, preview, renderer } = environment();
  const viewer = mountMarkdownViewer(preview, "## 설치\n\n### 실행", {
    renderer,
    renderOptions: { showToc: true },
  });
  assert.equal(viewer.article.tagName, "ARTICLE");
  assert.equal(preview.querySelector("nav").getAttribute("aria-label"), "문서 목차");
  assert.equal(preview.querySelectorAll(".markdown-toc a").length, 2);
  assert.equal(preview.querySelector("h2").id, "설치");
  viewer.update("<b>literal</b>");
  assert.equal(preview.querySelector("b"), null);
  assert.match(preview.textContent, /<b>literal<\/b>/);
});

test("preview and viewer produce the exact same sanitized HTML", () => {
  const { textarea, preview, renderer } = environment();
  textarea.value = "## 제목\n\n<script>alert(1)</script>";
  const shared = renderer.render(textarea.value);
  const attached = attachMarkdownPreview(textarea, preview, {
    renderer,
    mode: "preview",
    debounceMs: 0,
  });
  assert.equal(
    preview.querySelector(".markdown-viewer__content").innerHTML,
    shared,
  );
  assert.equal(preview.querySelector("script"), null);
  attached.destroy();
});

test("preview modes update visibility and compact profiles stay isolated", () => {
  const { textarea, preview, renderer } = environment();
  const modes = [];
  const attached = attachMarkdownPreview(textarea, preview, {
    renderer,
    mode: "editor",
    availableModes: ["editor", "preview"],
    renderOptions: { compact: true },
    debounceMs: 0,
    onModeChange: (mode) => modes.push(mode),
  });
  assert.equal(preview.hidden, true);
  assert.equal(attached.toggle(), "preview");
  assert.equal(textarea.hidden, true);
  assert.equal(preview.hidden, false);
  assert.ok(preview.querySelector("article").classList.contains("markdown-viewer--compact"));
  assert.equal(attached.toggle(), "editor");
  assert.deepEqual(modes, ["preview", "editor"]);
});

test("preview defers rendering during composition and cleans up on destroy", () => {
  const { dom, textarea, preview, renderer } = environment();
  textarea.value = "처음";
  const attached = attachMarkdownPreview(textarea, preview, {
    renderer,
    mode: "preview",
    debounceMs: 0,
  });
  textarea.dispatchEvent(new dom.window.CompositionEvent("compositionstart", {
    bubbles: true,
  }));
  textarea.value = "**조합 중**";
  textarea.dispatchEvent(new dom.window.InputEvent("input", { bubbles: true }));
  assert.doesNotMatch(preview.innerHTML, /<strong>/);
  textarea.dispatchEvent(new dom.window.CompositionEvent("compositionend", {
    bubbles: true,
  }));
  assert.match(preview.innerHTML, /<strong>조합 중<\/strong>/);
  attached.destroy();
  assert.equal(preview.children.length, 0);
  assert.equal(textarea.hidden, false);
});

