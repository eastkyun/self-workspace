import { RENDER_LIMITS } from "./contracts.js";

const TYPE_PATTERN = /^[a-z][a-z0-9-]*$/u;
const TARGET_PATTERN = /^[1-9][0-9]*$/u;
const TOKEN_PATTERN = /\[\[([a-z][a-z0-9-]*):([1-9][0-9]*)(?:\|([^\]\n]+))?\]\]/gu;

export function internalLinkKey(type, target) {
  return `${type}:${target}`;
}

export function parseInternalLinkToken(raw) {
  const match = /^\[\[([a-z][a-z0-9-]*):([1-9][0-9]*)(?:\|([^\]\n]+))?\]\]$/u
    .exec(String(raw ?? ""));
  if (!match) {
    return null;
  }
  return Object.freeze({
    type: match[1],
    target: match[2],
    label: match[3]?.trim() || null,
    raw: match[0],
  });
}

export function collectInternalLinks(markdown) {
  const unique = new Map();
  for (const match of String(markdown ?? "").matchAll(TOKEN_PATTERN)) {
    const reference = {
      type: match[1],
      target: match[2],
    };
    unique.set(
      internalLinkKey(reference.type, reference.target),
      Object.freeze(reference),
    );
    if (unique.size >= RENDER_LIMITS.maxInternalLinks) {
      break;
    }
  }
  return Object.freeze([...unique.values()]);
}

export function isSafeResolvedHref(href) {
  const value = String(href ?? "");
  if (!value.startsWith("/apps/") || value.includes("\\") || value.includes("//")) {
    return false;
  }
  try {
    const url = new URL(value, "https://workspace.invalid");
    if (url.origin !== "https://workspace.invalid") {
      return false;
    }
    let decoded = decodeURIComponent(url.pathname);
    if (decodeURIComponent(decoded) !== decoded) {
      return false;
    }
    return !decoded.split("/").some((segment) => segment === "." || segment === "..");
  } catch {
    return false;
  }
}

export function normalizeResolutionSnapshot(results = []) {
  const snapshot = new Map();
  for (const result of results) {
    if (!TYPE_PATTERN.test(result?.type)
        || !TARGET_PATTERN.test(result?.target)) {
      continue;
    }
    const exists = result.exists === true && isSafeResolvedHref(result.href);
    snapshot.set(
      internalLinkKey(result.type, result.target),
      Object.freeze({
        type: result.type,
        target: result.target,
        exists,
        href: exists ? result.href : null,
        label: String(result.label || `${result.type}:${result.target}`),
      }),
    );
  }
  return snapshot;
}

export async function resolveInternalLinks(references, {
  endpoint = "/api/internal-links/resolve",
  fetchImpl = globalThis.fetch,
  signal,
} = {}) {
  const unique = [...new Map(
    references.map((reference) => [
      internalLinkKey(reference.type, reference.target),
      { type: reference.type, target: reference.target },
    ]),
  ).values()];
  if (!unique.length) {
    return new Map();
  }
  if (unique.length > RENDER_LIMITS.maxInternalLinks) {
    throw new RangeError(
      `at most ${RENDER_LIMITS.maxInternalLinks} internal links may be resolved`,
    );
  }
  if (typeof fetchImpl !== "function") {
    throw new TypeError("internal link resolution requires fetch");
  }
  const response = await fetchImpl(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ links: unique }),
    signal,
  });
  if (!response.ok) {
    throw new Error(`internal link resolution failed: ${response.status}`);
  }
  return normalizeResolutionSnapshot(await response.json());
}

