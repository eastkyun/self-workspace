export const RENDER_LIMITS = Object.freeze({
  maxSourceLength: 200_000,
  maxInternalLinks: 50,
  minTocLevel: 2,
  maxTocLevel: 4,
});

export const DEFAULT_RENDER_OPTIONS = Object.freeze({
  breaks: true,
  headingAnchors: true,
  showToc: false,
  openExternalLinks: false,
  compact: false,
  emptyMessage: "",
  minTocLevel: RENDER_LIMITS.minTocLevel,
  maxTocLevel: RENDER_LIMITS.maxTocLevel,
});

export const VIEW_MODES = Object.freeze([
  "editor",
  "split",
  "preview",
]);

export function normalizeRenderOptions(options = {}) {
  const normalized = {
    ...DEFAULT_RENDER_OPTIONS,
    ...options,
  };
  if (!Number.isInteger(normalized.minTocLevel)
      || !Number.isInteger(normalized.maxTocLevel)
      || normalized.minTocLevel < 1
      || normalized.maxTocLevel > 6
      || normalized.minTocLevel > normalized.maxTocLevel) {
    throw new TypeError("invalid TOC heading range");
  }
  return Object.freeze(normalized);
}

export function assertViewMode(mode, availableModes = VIEW_MODES) {
  if (!availableModes.includes(mode)) {
    throw new TypeError(`unsupported editor view mode: ${mode}`);
  }
  return mode;
}

