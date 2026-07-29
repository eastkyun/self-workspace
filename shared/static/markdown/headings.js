const COMBINING_MARKS = /\p{Mark}+/gu;
const PUNCTUATION_OR_SYMBOL = /[^\p{Letter}\p{Number}\s-]/gu;
const WHITESPACE_OR_DASH = /[\s-]+/gu;

export function normalizeHeadingText(value) {
  return String(value ?? "")
    .normalize("NFKC")
    .replace(/\s+/gu, " ")
    .trim();
}

export function headingSlugBase(value) {
  const normalized = normalizeHeadingText(value)
    .toLocaleLowerCase("en-US")
    .normalize("NFKD")
    .replace(COMBINING_MARKS, "")
    .normalize("NFC")
    .replace(PUNCTUATION_OR_SYMBOL, "")
    .replace(WHITESPACE_OR_DASH, "-")
    .replace(/^-+|-+$/gu, "");
  return normalized || "heading";
}

export function createHeadingSlugger() {
  const counts = new Map();
  return Object.freeze({
    slug(value) {
      const base = headingSlugBase(value);
      const count = (counts.get(base) ?? 0) + 1;
      counts.set(base, count);
      return count === 1 ? base : `${base}-${count}`;
    },
  });
}
