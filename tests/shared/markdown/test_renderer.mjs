import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { createMarkdownRenderer } from "../../../shared/static/markdown/renderer.js";

function renderer() {
  const dom = new JSDOM("");
  return createMarkdownRenderer({ window: dom.window });
}

test("renders the shared MVP Markdown dialect", () => {
  const html = renderer().render([
    "# 제목",
    "",
    "**굵게**와 *기울임*과 ~~취소~~",
    "",
    "- [x] 완료",
    "- [ ] 대기",
    "",
    "| A | B |",
    "| - | - |",
    "| 1 | 2 |",
    "",
    "```js",
    "alert('text only')",
    "```",
  ].join("\n"));

  assert.match(html, /<h1 id="제목">제목 .*class="heading-permalink".*<\/h1>/);
  assert.match(html, /<strong>굵게<\/strong>/);
  assert.match(html, /<em>기울임<\/em>/);
  assert.match(html, /<del>취소<\/del>/);
  assert.match(html, /role="checkbox" aria-checked="true"/);
  assert.match(html, /aria-checked="true" aria-label="완료"/);
  assert.match(html, /aria-checked="false" aria-label="미완료"/);
  assert.doesNotMatch(html, /<input/i);
  assert.match(html, /<table>/);
  assert.match(html, /<code class="language-js">/);
});

test("raw HTML tokens are escaped as literal visible text", () => {
  const html = renderer().render([
    "<section>",
    "<script>alert(1)</script>",
    "<b>literal</b>",
    "</section>",
  ].join("\n"));

  assert.doesNotMatch(html, /<(?:section|script|b)\b/i);
  assert.match(html, /&lt;section&gt;/);
  assert.ok(html.includes("&lt;script&gt;alert(1)&lt;/script&gt;"));
  assert.ok(html.includes("&lt;b&gt;literal&lt;/b&gt;"));

  const document = new JSDOM(`<main>${html}</main>`).window.document;
  assert.equal(document.querySelector("script"), null);
  assert.match(document.querySelector("main").textContent, /<b>literal<\/b>/);
});

test("soft line breaks preserve existing plain text layout", () => {
  const html = renderer().render("첫째 줄\n둘째 줄");
  assert.match(html, /첫째 줄<br>둘째 줄/);
});

test("external link target policy is opt-in and always protects opener", () => {
  const shared = renderer();
  const normal = shared.render("[site](https://example.com)");
  const newWindow = shared.render(
    "[site](https://example.com)",
    { openExternalLinks: true },
  );

  assert.match(normal, /rel="noopener noreferrer"/);
  assert.doesNotMatch(normal, /target="_blank"/);
  assert.match(newWindow, /target="_blank"/);
  assert.match(newWindow, /rel="noopener noreferrer"/);
});

test("renderer instances and options do not leak mutable state", () => {
  const first = renderer();
  const second = renderer();
  assert.ok(first.render("a\nb", { breaks: false }).includes("a\nb"));
  assert.match(second.render("a\nb"), /a<br>b/);
});

test("render metadata carries a sanitized fragment equivalent to HTML", () => {
  const shared = renderer();
  const result = shared.renderWithMetadata("## 안전\n\n<script>alert(1)</script>");
  const container = result.fragment.ownerDocument.createElement("div");
  container.replaceChildren(result.fragment.cloneNode(true));

  assert.equal(container.innerHTML, result.html);
  assert.equal(container.querySelector("script"), null);
});

test("oversized source uses a safe plain-text fallback with a warning", () => {
  const source = `${"x".repeat(200_001)}<script>window.__xss = true</script>`;
  const result = renderer().renderWithMetadata(source);

  assert.match(result.html, /^<pre class="markdown-source-fallback">/);
  assert.match(result.html, /&lt;script&gt;window\.__xss = true&lt;\/script&gt;/);
  assert.doesNotMatch(result.html, /<script>/);
  assert.deepEqual(result.headings, []);
  assert.deepEqual(result.internalLinks, []);
  assert.equal(result.warnings[0].code, "source-too-large");
});
