import { Marked } from "./vendor/marked.esm.js";

import {
  RENDER_LIMITS,
  normalizeRenderOptions,
} from "./contracts.js";
import { createHeadingSlugger, normalizeHeadingText } from "./headings.js";
import {
  internalLinkKey,
  isSafeResolvedHref,
  parseInternalLinkToken,
} from "./internal-links.js";
import { createMarkdownSanitizer } from "./sanitizer.js";
import { buildToc } from "./toc.js";

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

function tokenText(token) {
  if (Array.isArray(token?.tokens)) {
    return token.tokens.map(tokenText).join("");
  }
  return String(token?.text ?? "");
}

function createMarked(options, state) {
  const slugger = createHeadingSlugger();
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
      heading(token) {
        const text = normalizeHeadingText(tokenText(token));
        const id = slugger.slug(text);
        state.headings.push(Object.freeze({
          level: token.depth,
          text,
          id,
        }));
        const anchor = options.headingAnchors
          ? ` id="${escapeHtml(id)}"`
          : "";
        return `<h${token.depth}${anchor}>${this.parser.parseInline(token.tokens)}</h${token.depth}>\n`;
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
    extensions: [
      {
        name: "internal-link",
        level: "inline",
        start(source) {
          const index = source.indexOf("[[");
          return index >= 0 ? index : undefined;
        },
        tokenizer(source) {
          const match = /^\[\[[^\]\n]+\]\]/u.exec(source);
          if (!match) {
            return undefined;
          }
          const parsed = parseInternalLinkToken(match[0]);
          if (!parsed) {
            return undefined;
          }
          return {
            type: "internal-link",
            raw: parsed.raw,
            referenceType: parsed.type,
            target: parsed.target,
            label: parsed.label,
          };
        },
        renderer(token) {
          const reference = Object.freeze({
            type: token.referenceType,
            target: token.target,
          });
          state.internalLinks.push(reference);
          const resolved = options.internalLinkResolutions?.get?.(
            internalLinkKey(reference.type, reference.target),
          );
          const label = escapeHtml(
            token.label
              || resolved?.label
              || `${reference.type}:${reference.target}`,
          );
          if (resolved?.exists && isSafeResolvedHref(resolved.href)) {
            return `<a class="internal-link" href="${escapeHtml(resolved.href)}">${label}</a>`;
          }
          return `<span class="internal-link broken" role="link" aria-disabled="true">${label}</span>`;
        },
      },
    ],
  });
  return marked;
}

export function createMarkdownRenderer({
  window: windowLike = globalThis.window,
} = {}) {
  const sanitizer = createMarkdownSanitizer(windowLike);

  function parse(markdown, renderOptions = {}, inline = false) {
    const source = String(markdown ?? "");
    if (source.length > RENDER_LIMITS.maxSourceLength) {
      throw new RangeError(
        `Markdown exceeds ${RENDER_LIMITS.maxSourceLength} characters`,
      );
    }
    const options = normalizeRenderOptions(renderOptions);
    const state = { headings: [], internalLinks: [] };
    const marked = createMarked(options, state);
    const rawHtml = inline ? marked.parseInline(source) : marked.parse(source);
    const html = sanitizer.sanitize(rawHtml);
    const headings = Object.freeze([...state.headings]);
    return Object.freeze({
      html,
      headings,
      toc: Object.freeze(buildToc(headings, {
        minLevel: options.minTocLevel,
        maxLevel: options.maxTocLevel,
      })),
      internalLinks: Object.freeze([...state.internalLinks]),
      warnings: Object.freeze([]),
    });
  }

  function render(markdown, renderOptions = {}) {
    return parse(markdown, renderOptions).html;
  }

  function renderInline(markdown, renderOptions = {}) {
    return parse(markdown, renderOptions, true).html;
  }

  function renderWithMetadata(markdown, renderOptions = {}) {
    return parse(markdown, renderOptions);
  }

  function collectHeadings(markdown, renderOptions = {}) {
    return renderWithMetadata(markdown, renderOptions).headings;
  }

  function extractPlainText(markdown) {
    const html = render(markdown, { headingAnchors: false });
    const container = windowLike.document.createElement("div");
    container.innerHTML = html;
    return (container.textContent ?? "")
      .replace(/\s+/gu, " ")
      .trim();
  }

  function extractExcerpt(markdown, maxLength = 160) {
    if (!Number.isInteger(maxLength) || maxLength < 1) {
      throw new TypeError("excerpt maxLength must be a positive integer");
    }
    const text = extractPlainText(markdown);
    const characters = Array.from(text);
    if (characters.length <= maxLength) {
      return text;
    }
    return `${characters.slice(0, maxLength).join("").trimEnd()}…`;
  }

  return Object.freeze({
    collectHeadings,
    extractExcerpt,
    extractPlainText,
    render,
    renderInline,
    renderWithMetadata,
  });
}
