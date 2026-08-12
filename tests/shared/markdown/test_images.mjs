import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import {
  collectAssetImages,
  isSafeAssetHref,
  normalizeAssetResolutionSnapshot,
  resolveAssetImages,
} from "../../../shared/static/markdown/images.js";
import { createMarkdownRenderer } from "../../../shared/static/markdown/renderer.js";

function renderer() {
  return createMarkdownRenderer({ window: new JSDOM("").window });
}

test("collects unique image asset IDs and validates local content routes", () => {
  assert.deepEqual(
    collectAssetImages("![a](image://3) ![b](image://3) ![c](image://9)"),
    ["3", "9"],
  );
  assert.equal(isSafeAssetHref("/api/assets/3/content", "3"), true);
  assert.equal(isSafeAssetHref("/api/assets/4/content", "3"), false);
  assert.equal(isSafeAssetHref("https://evil.test/api/assets/3/content", "3"), false);
  assert.equal(isSafeAssetHref("/api/assets/%33/content", "3"), false);
  assert.equal(
    isSafeAssetHref("/api/v1/workspaces/11111111-1111-1111-1111-111111111111/assets/3/content", "3"),
    true,
  );
  assert.equal(
    isSafeAssetHref("/api/publications/public/22222222-2222-2222-2222-222222222222/assets/3/content", "3"),
    true,
  );
});

test("renderer emits only resolver-approved local images", () => {
  const resolutions = normalizeAssetResolutionSnapshot([
    {
      id: 3,
      exists: true,
      href: "/api/assets/3/content",
      media_type: "image/png",
    },
  ]);
  const safe = renderer().render(
    '![diagram](image://3 "Architecture")',
    { assetImageResolutions: resolutions },
  );
  const unresolved = renderer().render("![private](image://4)");
  const remote = renderer().render("![remote](https://evil.test/x.png)");

  assert.match(safe, /<img class="markdown-image"/);
  assert.match(safe, /src="\/api\/assets\/3\/content"/);
  assert.match(safe, /loading="lazy"/);
  assert.doesNotMatch(unresolved, /<img/);
  assert.match(unresolved, /markdown-image-broken/);
  assert.doesNotMatch(remote, /<img/);
  assert.doesNotMatch(remote, /evil\.test\/x\.png/);

  const canonicalHref = "/api/v1/workspaces/11111111-1111-1111-1111-111111111111/assets/3/content";
  const canonicalResolutions = normalizeAssetResolutionSnapshot([
    { id: 3, exists: true, href: canonicalHref, media_type: "image/png" },
  ]);
  const canonical = renderer().render("![workspace image](image://3)", {
    assetImageResolutions: canonicalResolutions,
  });
  assert.match(canonical, new RegExp(`src="${canonicalHref}"`));
});

test("asset resolver posts a bounded ID batch and rejects unsafe results", async () => {
  let request;
  const snapshot = await resolveAssetImages(["3", "3"], {
    endpoint: "/api/assets/resolve",
    fetchImpl: async (url, options) => {
      request = { url, options };
      return {
        ok: true,
        json: async () => [
          { id: 3, exists: true, href: "/api/assets/3/content", media_type: "image/png" },
          { id: 4, exists: true, href: "javascript:alert(1)", media_type: "image/png" },
        ],
      };
    },
  });
  assert.equal(request.url, "/api/assets/resolve");
  assert.deepEqual(JSON.parse(request.options.body), { ids: [3] });
  assert.equal(snapshot.get("3").exists, true);
  assert.equal(snapshot.get("4").exists, false);
});
