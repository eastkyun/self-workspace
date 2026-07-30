import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import {
  collectInternalLinks,
  normalizeResolutionSnapshot,
  parseInternalLinkToken,
  resolveInternalLinks,
} from "../../../shared/static/markdown/internal-links.js";
import { createMarkdownRenderer } from "../../../shared/static/markdown/renderer.js";

test("parser accepts positive-decimal and canonical-slug tokens", () => {
  assert.deepEqual(parseInternalLinkToken("[[note:13|회의 기록]]"), {
    type: "note",
    target: "13",
    label: "회의 기록",
    raw: "[[note:13|회의 기록]]",
  });
  assert.deepEqual(parseInternalLinkToken("[[manual:install-guide|설치]]"), {
    type: "manual",
    target: "install-guide",
    label: "설치",
    raw: "[[manual:install-guide|설치]]",
  });
  assert.equal(parseInternalLinkToken("[[note:../secret]]"), null);
  assert.equal(parseInternalLinkToken("[[manual:Install-Guide]]"), null);
  assert.equal(parseInternalLinkToken("[[manual:install--guide]]"), null);
  assert.equal(parseInternalLinkToken("[[Note:1]]"), null);
});

test("collector deduplicates references in deterministic first-seen order", () => {
  assert.deepEqual(
    collectInternalLinks("[[note:2]] [[calendar:1]] [[note:2|다시]]"),
    [
      { type: "note", target: "2" },
      { type: "calendar", target: "1" },
    ],
  );
});

test("renderer uses a generic resolution snapshot and marks broken links", () => {
  const dom = new JSDOM("");
  const renderer = createMarkdownRenderer({ window: dom.window });
  const resolutions = normalizeResolutionSnapshot([
    {
      type: "note",
      target: "2",
      exists: true,
      href: "/apps/notes/?note_id=2",
      label: "서버 노트",
    },
  ]);
  const result = renderer.renderWithMetadata(
    "[[note:2]] [[calendar:1|일정]]",
    { internalLinkResolutions: resolutions },
  );
  assert.match(result.html, /class="internal-link" href="\/apps\/notes\/\?note_id=2">서버 노트/);
  assert.match(result.html, /class="internal-link broken" role="link" aria-disabled="true">일정/);
  assert.deepEqual(result.internalLinks, [
    { type: "note", target: "2" },
    { type: "calendar", target: "1" },
  ]);
});

test("unsafe resolver href remains broken before and after sanitization", () => {
  const dom = new JSDOM("");
  const renderer = createMarkdownRenderer({ window: dom.window });
  const resolutions = normalizeResolutionSnapshot([
    {
      type: "note",
      target: "1",
      exists: true,
      href: "javascript:alert(1)",
      label: "<img onerror=alert(1)>",
    },
  ]);
  const html = renderer.render("[[note:1]]", {
    internalLinkResolutions: resolutions,
  });
  assert.doesNotMatch(html, /<a|<img|javascript:/i);
  assert.match(html, /&lt;img onerror=alert\(1\)&gt;/);
});

test("batch client posts deduplicated references and validates routes", async () => {
  let request;
  const snapshot = await resolveInternalLinks([
    { type: "note", target: "2" },
    { type: "note", target: "2" },
  ], {
    endpoint: "/api/internal-links/resolve",
    fetchImpl: async (url, options) => {
      request = { url, options };
      return {
        ok: true,
        async json() {
          return [{
            type: "note",
            target: "2",
            exists: true,
            href: "/apps/notes/?note_id=2",
            label: "노트",
          }];
        },
      };
    },
  });
  assert.equal(request.url, "/api/internal-links/resolve");
  assert.deepEqual(JSON.parse(request.options.body), {
    links: [{ type: "note", target: "2" }],
  });
  assert.equal(snapshot.get("note:2").exists, true);
});

test("safe resolved href accepts local publication routes and rejects bypasses", () => {
  const snapshot = normalizeResolutionSnapshot([
    {
      type: "manual",
      target: "install-guide",
      exists: true,
      href: "/manual/install-guide",
      label: "설치",
    },
    {
      type: "wiki",
      target: "traversal",
      exists: true,
      href: "/wiki/../secret",
      label: "우회",
    },
    {
      type: "wiki",
      target: "encoded",
      exists: true,
      href: "/wiki/%2e%2e/secret",
      label: "인코딩 우회",
    },
    {
      type: "wiki",
      target: "host",
      exists: true,
      href: "//evil.example/wiki/host",
      label: "호스트 우회",
    },
  ]);
  assert.equal(snapshot.get("manual:install-guide").exists, true);
  assert.equal(snapshot.get("wiki:traversal").exists, false);
  assert.equal(snapshot.get("wiki:encoded").exists, false);
  assert.equal(snapshot.get("wiki:host").exists, false);
});
