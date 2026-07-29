import { assertViewMode } from "./contracts.js";
import { mountMarkdownViewer } from "./viewer.js";

export function attachMarkdownPreview(textarea, container, {
  renderer,
  renderOptions = {},
  availableModes = ["editor", "split", "preview"],
  mode: initialMode = "editor",
  debounceMs = 120,
  onModeChange = () => {},
} = {}) {
  if (!textarea?.ownerDocument || !container) {
    throw new TypeError("preview requires a textarea and container");
  }
  let mode = assertViewMode(initialMode, availableModes);
  let composing = false;
  let destroyed = false;
  let timer = null;
  const viewer = mountMarkdownViewer(container, textarea.value, {
    renderer,
    renderOptions,
    ariaLabel: "Markdown 미리보기",
  });

  function applyMode() {
    textarea.hidden = mode === "preview";
    container.hidden = mode === "editor";
    const host = textarea.closest("[data-markdown-editor]")
      ?? textarea.parentElement;
    host?.setAttribute("data-markdown-view-mode", mode);
  }

  function refresh() {
    if (destroyed) {
      throw new Error("Markdown preview has been destroyed");
    }
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
    return viewer.update(textarea.value);
  }

  function schedule() {
    if (destroyed || composing) {
      return;
    }
    if (timer) {
      clearTimeout(timer);
    }
    if (debounceMs === 0) {
      refresh();
      return;
    }
    timer = setTimeout(refresh, debounceMs);
  }

  function setMode(nextMode) {
    if (destroyed) {
      throw new Error("Markdown preview has been destroyed");
    }
    mode = assertViewMode(nextMode, availableModes);
    applyMode();
    if (mode !== "editor") {
      refresh();
    }
    onModeChange(mode);
    return mode;
  }

  function toggle() {
    const next = mode === "editor"
      ? (availableModes.includes("split") ? "split" : "preview")
      : mode === "split"
        ? "preview"
        : "editor";
    return setMode(next);
  }

  function handleCompositionStart() {
    composing = true;
  }

  function handleCompositionEnd() {
    composing = false;
    schedule();
  }

  textarea.addEventListener("input", schedule);
  textarea.addEventListener("compositionstart", handleCompositionStart);
  textarea.addEventListener("compositionend", handleCompositionEnd);
  applyMode();

  return Object.freeze({
    destroy() {
      if (destroyed) {
        return;
      }
      destroyed = true;
      if (timer) {
        clearTimeout(timer);
      }
      textarea.removeEventListener("input", schedule);
      textarea.removeEventListener("compositionstart", handleCompositionStart);
      textarea.removeEventListener("compositionend", handleCompositionEnd);
      textarea.hidden = false;
      container.hidden = false;
      viewer.destroy();
    },
    getMode: () => mode,
    refresh,
    setMode,
    toggle,
    viewer,
  });
}

