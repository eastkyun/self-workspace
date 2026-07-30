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
import { assetImageKey, isSafeAssetHref } from "./images.js";

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
  if (normalized.includes("\\")) {
    return false;
  }
  return /^(?:(?:https?|mailto):|[#/?](?!\/)|\.{1,2}\/)/i.test(normalized);
}

function tokenText(token, options) {
  if (token?.type === "internal-link") {
    const resolved = options.internalLinkResolutions?.get?.(
      internalLinkKey(token.referenceType, token.target),
    );
    return String(
      token.label
        || resolved?.label
        || `${token.referenceType}:${token.target}`,
    );
  }
  if (Array.isArray(token?.tokens)) {
    return token.tokens.map((child) => tokenText(child, options)).join("");
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
        const text = normalizeHeadingText(tokenText(token, options));
        const id = slugger.slug(text);
        state.headings.push(Object.freeze({
          level: token.depth,
          text,
          id,
        }));
        const anchor = options.headingAnchors
          ? ` id="${escapeHtml(id)}"`
          : "";
        const permalink = options.headingAnchors
          ? ` <a class="heading-permalink" href="#${escapeHtml(id)}" aria-label="${escapeHtml(`“${text}” 섹션 링크`)}"></a>`
          : "";
        return `<h${token.depth}${anchor}>${this.parser.parseInline(token.tokens)}${permalink}</h${token.depth}>\n`;
      },
      checkbox({ checked }) {
        const state = checked ? "true" : "false";
        const symbol = checked ? "✓" : "";
        const label = checked ? "완료" : "미완료";
        return `<span class="markdown-task-checkbox" role="checkbox" aria-checked="${state}" aria-label="${label}">${symbol}</span> `;
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
      image(token) {
        const match = /^image:\/\/([1-9][0-9]*)$/u.exec(
          String(token.href ?? ""),
        );
        const alt = String(token.text ?? "");
        if (!match) {
          return `<span class="markdown-image-broken">${escapeHtml(
            alt || `![image](${token.href ?? ""})`,
          )}</span>`;
        }
        const assetId = match[1];
        state.assetImages.push(assetId);
        const resolved = options.assetImageResolutions?.get?.(
          assetImageKey(assetId),
        );
        if (!resolved?.exists || !isSafeAssetHref(resolved.href, assetId)) {
          return `<span class="markdown-image-broken" role="img" aria-label="${escapeHtml(
            alt || `이미지 ${assetId}`,
          )}">${escapeHtml(alt || `이미지 ${assetId}을 불러올 수 없습니다.`)}</span>`;
        }
        const title = token.title
          ? ` title="${escapeHtml(token.title)}"`
          : "";
        return `<img class="markdown-image" src="${escapeHtml(
          resolved.href,
        )}" alt="${escapeHtml(alt)}"${title} loading="lazy" decoding="async">`;
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
    const options = normalizeRenderOptions(renderOptions);
    if (source.length > RENDER_LIMITS.maxSourceLength) {
      const fallback = inline
        ? escapeHtml(source)
        : `<pre class="markdown-source-fallback">${escapeHtml(source)}</pre>`;
      const fragment = sanitizer.sanitizeToFragment(fallback);
      return Object.freeze({
        html: sanitizer.serialize(fragment),
        fragment,
        headings: Object.freeze([]),
        toc: Object.freeze([]),
        internalLinks: Object.freeze([]),
        assetImages: Object.freeze([]),
        warnings: Object.freeze([
          Object.freeze({
            code: "source-too-large",
            message: `Markdown exceeds ${RENDER_LIMITS.maxSourceLength} characters and was shown as plain text.`,
          }),
        ]),
      });
    }
    const state = { headings: [], internalLinks: [], assetImages: [] };
    const marked = createMarked(options, state);
    const rawHtml = inline ? marked.parseInline(source) : marked.parse(source);
    const fragment = sanitizer.sanitizeToFragment(rawHtml);
    const html = sanitizer.serialize(fragment);
    const headings = Object.freeze([...state.headings]);
    return Object.freeze({
      html,
      fragment,
      headings,
      toc: Object.freeze(buildToc(headings, {
        minLevel: options.minTocLevel,
        maxLevel: options.maxTocLevel,
      })),
      internalLinks: Object.freeze([...state.internalLinks]),
      assetImages: Object.freeze([...new Set(state.assetImages)]),
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
