import { Marked } from "./vendor/marked.esm.js";

import {
  RENDER_LIMITS,
  normalizeRenderOptions,
} from "./contracts.js";
import { createMarkdownSanitizer } from "./sanitizer.js";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function isExternalHref(href) {
  return /^https?:\/\//i.test(href);
}

function isAllowedHref(href) {
  const normalized = String(href ?? "")
    .trim()
    .replace(/[\u0000-\u001F\u007F-\u009F\s]/g, "");
  return /^(?:(?:https?|mailto):|[#/?](?!\/)|\.{1,2}\/)/i.test(normalized);
}

function createMarked(options) {
  const marked = new Marked({
    async: false,
    breaks: options.breaks,
    gfm: true,
    pedantic: false,
    renderer: {
      html(token) {
        // Raw HTML is preserved as readable source text. It must never enter the
        // generated HTML tree before DOMPurify sees the complete render output.
        return escapeHtml(token.text);
      },
      checkbox({ checked }) {
        const state = checked ? "true" : "false";
        const symbol = checked ? "✓" : "";
        return `<span class="markdown-task-checkbox" role="checkbox" aria-checked="${state}">${symbol}</span> `;
      },
      link(token) {
        const label = this.parser.parseInline(token.tokens);
        if (!isAllowedHref(token.href)) {
          return label;
        }
        const href = escapeHtml(token.href);
        const title = token.title
          ? ` title="${escapeHtml(token.title)}"`
          : "";
        const external = isExternalHref(token.href);
        const target = external && options.openExternalLinks
          ? ' target="_blank"'
          : "";
        const rel = external ? ' rel="noopener noreferrer"' : "";
        return `<a href="${href}"${title}${target}${rel}>${label}</a>`;
      },
    },
  });
  return marked;
}

export function createMarkdownRenderer({
  window: windowLike = globalThis.window,
} = {}) {
  const sanitizer = createMarkdownSanitizer(windowLike);

  function render(markdown, renderOptions = {}) {
    const source = String(markdown ?? "");
    if (source.length > RENDER_LIMITS.maxSourceLength) {
      throw new RangeError(
        `Markdown exceeds ${RENDER_LIMITS.maxSourceLength} characters`,
      );
    }
    const options = normalizeRenderOptions(renderOptions);
    const marked = createMarked(options);
    const rawHtml = marked.parse(source);
    return sanitizer.sanitize(rawHtml);
  }

  function renderInline(markdown, renderOptions = {}) {
    const source = String(markdown ?? "");
    if (source.length > RENDER_LIMITS.maxSourceLength) {
      throw new RangeError(
        `Markdown exceeds ${RENDER_LIMITS.maxSourceLength} characters`,
      );
    }
    const options = normalizeRenderOptions(renderOptions);
    const marked = createMarked(options);
    return sanitizer.sanitize(marked.parseInline(source));
  }

  return Object.freeze({
    render,
    renderInline,
  });
}

