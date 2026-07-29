import assert from "node:assert/strict";
import { performance } from "node:perf_hooks";
import test from "node:test";

import { JSDOM } from "jsdom";

import { attachMarkdownEditor } from "../../../shared/static/markdown/editor-controller.js";
import { createMarkdownRenderer } from "../../../shared/static/markdown/renderer.js";

test("strict sanitizer blocks encoded and malformed attack variants", () => {
  const dom = new JSDOM("");
  const renderer = createMarkdownRenderer({ window: dom.window });
  const attacks = [
    "[encoded](java&#x73;cript:alert(1))",
    "[control](java\\u0000script:alert(1))",
    "<svg><foreignObject><script>alert(1)</script></foreignObject></svg>",
    "<p style=\"background:url(javascript:alert(1))\">text</p>",
    "<a href=\"&#106;avascript:alert(1)\" onclick=\"alert(1)\">x</a>",
    "<img src=x onerror=alert(1)>",
  ];
  for (const attack of attacks) {
    const html = renderer.render(attack);
    const document = new JSDOM(`<main>${html}</main>`).window.document;
    assert.equal(
      document.querySelector(
        "script, svg, foreignObject, img, [onclick], [onerror], [style]",
      ),
      null,
      attack,
    );
    for (const anchor of document.querySelectorAll("a[href]")) {
      assert.doesNotMatch(anchor.getAttribute("href"), /javascript:/i);
    }
  }
});

test("100KB documents, many headings and links render within a bounded budget", () => {
  const dom = new JSDOM("");
  const renderer = createMarkdownRenderer({ window: dom.window });
  const headings = Array.from(
    { length: 100 },
    (_, index) => `## 제목 ${index % 10}`,
  ).join("\n\n");
  const links = Array.from(
    { length: 50 },
    (_, index) => `[[note:${index + 1}]]`,
  ).join(" ");
  const source = `${headings}\n\n${links}\n\n${"문서 내용 ".repeat(9_000)}`;

  const started = performance.now();
  const result = renderer.renderWithMetadata(source);
  const elapsed = performance.now() - started;

  assert.equal(result.headings.length, 100);
  assert.equal(result.internalLinks.length, 50);
  assert.ok(elapsed < 5_000, `render took ${elapsed.toFixed(1)}ms`);
});

test("multiple editor instances keep selections and lifecycle isolated", () => {
  const dom = new JSDOM("<textarea id=\"a\"></textarea><textarea id=\"b\"></textarea>");
  const firstElement = dom.window.document.querySelector("#a");
  const secondElement = dom.window.document.querySelector("#b");
  firstElement.value = "첫째";
  secondElement.value = "둘째";
  firstElement.setSelectionRange(0, 2);
  secondElement.setSelectionRange(0, 2);
  const first = attachMarkdownEditor(firstElement);
  const second = attachMarkdownEditor(secondElement);

  first.execute("bold");
  second.execute("italic");
  assert.equal(firstElement.value, "**첫째**");
  assert.equal(secondElement.value, "*둘째*");
  first.destroy();
  second.execute("bold");
  assert.equal(secondElement.value, "***둘째***");
  second.destroy();
});

