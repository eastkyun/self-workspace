import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import { JSDOM } from "jsdom";

import { createMarkdownRenderer } from "../../../shared/static/markdown/renderer.js";

const corpusDocument = JSON.parse(
  await readFile(new URL("../../fixtures/markdown/security.json", import.meta.url)),
);

function render(source) {
  const dom = new JSDOM("");
  return createMarkdownRenderer({ window: dom.window }).render(source);
}

for (const fixture of corpusDocument.cases) {
  test(`blocks attack corpus entry: ${fixture.id}`, () => {
    const html = render(fixture.markdown);
    const document = new JSDOM(`<main>${html}</main>`).window.document;
    const main = document.querySelector("main");

    assert.equal(main.querySelector("script, iframe, object, embed, form"), null);
    assert.equal(main.querySelector("[onerror], [onclick], [onload]"), null);
    for (const anchor of main.querySelectorAll("a[href]")) {
      assert.doesNotMatch(anchor.getAttribute("href"), /^(?:javascript|vbscript|data):/i);
    }
    if (fixture.id === "internal-traversal") {
      assert.equal(main.querySelector("a"), null);
    }
    if (fixture.literal_text) {
      assert.ok(main.textContent.includes(fixture.literal_text));
    }
  });
}

test("sanitizer removes unsafe elements and attributes from generated HTML", () => {
  const html = render("[safe](https://example.com)\n\n![x](x \"onerror=alert(1)\")");
  assert.doesNotMatch(html, /<img/i);
  assert.doesNotMatch(html, /onerror/i);
  assert.match(html, /href="https:\/\/example.com"/);
});

test("backslash relative links cannot normalize into an external host", () => {
  const html = render(String.raw`[unsafe](/\evil.example/path)`);
  const dom = new JSDOM(`<main>${html}</main>`, {
    url: "https://workspace.example/apps/notes/",
  });

  assert.equal(dom.window.document.querySelector("a"), null);
  assert.match(dom.window.document.querySelector("main").textContent, /unsafe/);
});
