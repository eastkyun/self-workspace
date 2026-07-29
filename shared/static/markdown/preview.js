import { assertViewMode } from "./contracts.js";
import { mountMarkdownViewer } from "./viewer.js";

let previewSequence = 0;

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
  let toggleButton = null;
  const host = textarea.closest("[data-markdown-editor]")
    ?? textarea.parentElement;
  if (!container.id) {
    container.id = `markdown-preview-${++previewSequence}`;
  }
  const status = textarea.ownerDocument.createElement("span");
  status.className = "sr-only markdown-preview-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  host?.append(status);
  const viewer = mountMarkdownViewer(container, textarea.value, {
    renderer,
    renderOptions,
    ariaLabel: "Markdown 미리보기",
  });

  function applyMode() {
    textarea.hidden = mode === "preview";
    container.hidden = mode === "editor";
    host?.setAttribute("data-markdown-view-mode", mode);
    if (toggleButton) {
      toggleButton.setAttribute("aria-pressed", String(mode !== "editor"));
    }
    status.textContent = {
      editor: "Markdown 편집 모드",
      split: "Markdown 편집 및 미리보기 모드",
      preview: "Markdown 미리보기 모드",
    }[mode];
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
      status.remove();
      viewer.destroy();
    },
    getMode: () => mode,
    refresh,
    setRenderOptions(nextRenderOptions) {
      if (destroyed) {
        throw new Error("Markdown preview has been destroyed");
      }
      viewer.setRenderOptions(nextRenderOptions);
      return refresh();
    },
    setMode,
    setToggleButton(button) {
      if (destroyed) {
        throw new Error("Markdown preview has been destroyed");
      }
      if (!button || button.dataset.markdownCommand !== "preview-toggle") {
        throw new TypeError("preview toggle button is required");
      }
      toggleButton = button;
      toggleButton.setAttribute("aria-controls", container.id);
      toggleButton.setAttribute("aria-pressed", String(mode !== "editor"));
      return toggleButton;
    },
    toggle,
    viewer,
  });
}
