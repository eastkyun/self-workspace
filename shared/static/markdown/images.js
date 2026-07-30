import { RENDER_LIMITS } from "./contracts.js";

const IMAGE_SOURCE_PATTERN = /!\[[^\]\n]*\]\(image:\/\/([1-9][0-9]*)(?:\s+"[^"\n]*")?\)/gu;

export function assetImageKey(assetId) {
  return String(assetId);
}

export function collectAssetImages(markdown) {
  const ids = new Set();
  for (const match of String(markdown ?? "").matchAll(IMAGE_SOURCE_PATTERN)) {
    ids.add(match[1]);
    if (ids.size >= RENDER_LIMITS.maxAssetImages) break;
  }
  return Object.freeze([...ids]);
}

export function isSafeAssetHref(href, expectedAssetId = null) {
  const value = String(href ?? "");
  if (!value.startsWith("/")
      || value.startsWith("//")
      || value.includes("\\")
      || value.includes("?")
      || value.includes("#")
      || /[\u0000-\u001F\u007F]/u.test(value)) {
    return false;
  }
  try {
    if (decodeURIComponent(value) !== value) return false;
  } catch {
    return false;
  }
  const match = /(?:^|\/)api\/assets\/([1-9][0-9]*)\/content$/u.exec(value);
  return Boolean(
    match
    && (expectedAssetId === null || match[1] === String(expectedAssetId)),
  );
}

export function normalizeAssetResolutionSnapshot(results = []) {
  const snapshot = new Map();
  for (const result of results) {
    const id = String(result?.id ?? "");
    if (!/^[1-9][0-9]*$/u.test(id)) continue;
    const exists = result.exists === true && isSafeAssetHref(result.href, id);
    snapshot.set(id, Object.freeze({
      id,
      exists,
      href: exists ? result.href : null,
      mediaType: exists ? String(result.media_type || "") : null,
    }));
  }
  return snapshot;
}

export async function resolveAssetImages(assetIds, {
  endpoint = "/api/assets/resolve",
  fetchImpl = globalThis.fetch,
  signal,
} = {}) {
  const ids = [...new Set(assetIds.map(String).filter(
    (id) => /^[1-9][0-9]*$/u.test(id),
  ))];
  if (!ids.length) return new Map();
  if (ids.length > RENDER_LIMITS.maxAssetImages) {
    throw new RangeError(
      `at most ${RENDER_LIMITS.maxAssetImages} asset images may be resolved`,
    );
  }
  const response = await fetchImpl(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ids: ids.map(Number) }),
    signal,
  });
  if (!response.ok) {
    throw new Error(`asset image resolution failed: ${response.status}`);
  }
  return normalizeAssetResolutionSnapshot(await response.json());
}
