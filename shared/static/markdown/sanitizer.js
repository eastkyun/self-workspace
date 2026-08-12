import createDOMPurify from "./vendor/purify.es.mjs";
import { isSafeAssetHref } from "./images.js";

const ALLOWED_TAGS = Object.freeze([
  "a",
  "blockquote",
  "br",
  "code",
  "del",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "img",
  "li",
  "ol",
  "p",
  "pre",
  "span",
  "strong",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "ul",
]);

const ALLOWED_ATTR = Object.freeze([
  "aria-checked",
  "aria-label",
  "class",
  "href",
  "src",
  "alt",
  "loading",
  "decoding",
  "id",
  "rel",
  "role",
  "target",
  "title",
]);

const SAFE_URI = /^(?:(?:https?|mailto):|[#/?](?!\/)|\.{1,2}\/)/i;

function isSafeHref(value) {
  const normalized = String(value ?? "")
    .trim()
    .replace(/[\u0000-\u001F\u007F-\u009F\s]/g, "");
  if (normalized.includes("\\")) {
    return false;
  }
  return SAFE_URI.test(normalized);
}

function isSafeImageSource(value) {
  return isSafeAssetHref(String(value ?? "").trim());
}

export function createMarkdownSanitizer(windowLike = globalThis.window) {
  if (!windowLike?.document) {
    throw new TypeError("a DOM window is required to sanitize Markdown");
  }

  const purifier = createDOMPurify(windowLike);
  purifier.addHook("uponSanitizeAttribute", (_node, data) => {
    if (data.attrName === "href" && !isSafeHref(data.attrValue)) {
      data.keepAttr = false;
    }
    if (data.attrName === "src" && !isSafeImageSource(data.attrValue)) {
      data.keepAttr = false;
    }
  });

  function sanitizeToFragment(html) {
    return purifier.sanitize(String(html ?? ""), {
      ALLOWED_TAGS,
      ALLOWED_ATTR,
      ALLOW_ARIA_ATTR: true,
      ALLOW_DATA_ATTR: false,
      ALLOW_UNKNOWN_PROTOCOLS: false,
      FORBID_TAGS: [
        "base",
        "button",
        "embed",
        "form",
        "iframe",
        "input",
        "link",
        "math",
        "meta",
        "object",
        "script",
        "style",
        "svg",
      ],
      FORBID_ATTR: ["style"],
      KEEP_CONTENT: true,
      RETURN_DOM_FRAGMENT: true,
      RETURN_TRUSTED_TYPE: false,
    });
  }

  function serialize(fragment) {
    const container = windowLike.document.createElement("div");
    container.replaceChildren(fragment.cloneNode(true));
    return container.innerHTML;
  }

  return Object.freeze({
    sanitize(html) {
      return serialize(sanitizeToFragment(html));
    },
    sanitizeToFragment,
    serialize,
  });
}
