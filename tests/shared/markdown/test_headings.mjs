import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import {
  createHeadingSlugger,
  headingSlugBase,
} from "../../../shared/static/markdown/headings.js";
import { createMarkdownRenderer } from "../../../shared/static/markdown/renderer.js";
import { buildToc } from "../../../shared/static/markdown/toc.js";

test("heading slugs are deterministic for Korean, Unicode and punctuation", () => {
  assert.equal(headingSlugBase("  설치 방법!  "), "설치-방법");
  assert.equal(headingSlugBase("CAFÉ & résumé"), "cafe-resume");
  assert.equal(headingSlugBase("!!!"), "heading");
});

test("duplicate headings receive stable numeric suffixes", () => {
  const slugger = createHeadingSlugger();
  assert.equal(slugger.slug("설치 방법"), "설치-방법");
  assert.equal(slugger.slug("설치 방법"), "설치-방법-2");
  assert.equal(slugger.slug("설치 방법"), "설치-방법-3");
});

test("renderer HTML, metadata and TOC share the same heading IDs", () => {
  const dom = new JSDOM("");
  const result = createMarkdownRenderer({ window: dom.window })
    .renderWithMetadata([
      "# 문서",
      "## 설치 방법",
      "#### 세부",
      "## 설치 방법",
    ].join("\n\n"));

  assert.deepEqual(
    result.headings.map(({ id }) => id),
    ["문서", "설치-방법", "세부", "설치-방법-2"],
  );
  for (const heading of result.headings) {
    assert.ok(result.html.includes(`id="${heading.id}"`));
  }
  assert.match(result.html, /class="heading-permalink"/);
  assert.match(result.html, /aria-label="“설치 방법” 섹션 링크"/);
  assert.deepEqual(
    result.toc.map(({ id, children }) => [
      id,
      children.map((child) => child.id),
    ]),
    [
      ["설치-방법", ["세부"]],
      ["설치-방법-2", []],
    ],
  );
});

test("internal links contribute their semantic label to heading metadata", () => {
  const dom = new JSDOM("");
  const result = createMarkdownRenderer({ window: dom.window })
    .renderWithMetadata("## [[note:1|프로젝트]]");

  assert.equal(result.headings[0].text, "프로젝트");
  assert.equal(result.headings[0].id, "프로젝트");
  assert.equal(result.toc[0].text, "프로젝트");
  assert.match(result.html, /id="프로젝트"/);
});

test("TOC tolerates skipped levels and filters its configured range", () => {
  const toc = buildToc([
    { level: 1, id: "one", text: "One" },
    { level: 2, id: "two", text: "Two" },
    { level: 4, id: "four", text: "Four" },
    { level: 5, id: "five", text: "Five" },
  ]);
  assert.deepEqual(toc, [{
    level: 2,
    id: "two",
    text: "Two",
    children: [{
      level: 4,
      id: "four",
      text: "Four",
      children: [],
    }],
  }]);
});

test("plain text and excerpts remove Markdown syntax without splitting Unicode", () => {
  const dom = new JSDOM("");
  const renderer = createMarkdownRenderer({ window: dom.window });
  assert.equal(
    renderer.extractPlainText("## 제목\n\n**굵게** [링크](/safe)"),
    "제목 굵게 링크",
  );
  assert.equal(renderer.extractExcerpt("가나다라마바사", 3), "가나다…");
  assert.equal(renderer.extractExcerpt("🙂🙂🙂", 2), "🙂🙂…");
});
