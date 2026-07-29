import { getCommand, searchCommands } from "./commands.js";
import { getEditorProfile } from "./profiles.js";

let paletteSequence = 0;

function slashContext(textarea) {
  const cursor = textarea.selectionStart;
  if (cursor !== textarea.selectionEnd) {
    return null;
  }
  const before = textarea.value.slice(0, cursor);
  const lineStart = before.lastIndexOf("\n") + 1;
  const currentLine = before.slice(lineStart);
  const match = /(^|\s)\/([^\s/]*)$/u.exec(currentLine);
  if (!match) {
    return null;
  }
  const slashOffset = match.index + match[1].length;
  const slashStart = lineStart + slashOffset;

  const fences = before.match(/^```/gmu)?.length ?? 0;
  if (fences % 2 === 1) {
    return null;
  }
  const beforeSlashOnLine = currentLine.slice(0, slashOffset);
  const inlineTicks = (beforeSlashOnLine.match(/(?<!\\)`/gu) ?? []).length;
  if (inlineTicks % 2 === 1) {
    return null;
  }
  return Object.freeze({
    start: slashStart,
    end: cursor,
    query: match[2],
    signature: `${slashStart}:${match[2]}`,
  });
}

function placePalette(palette, textarea) {
  const view = textarea.ownerDocument.defaultView;
  const rect = textarea.getBoundingClientRect();
  const maxLeft = Math.max(8, view.innerWidth - 280);
  const maxTop = Math.max(8, view.innerHeight - 260);
  palette.style.left = `${Math.max(8, Math.min(rect.left + 12, maxLeft))}px`;
  palette.style.top = `${Math.max(8, Math.min(rect.top + 44, maxTop))}px`;
}

export function attachSlashPalette(textarea, controller, {
  profile: profileId = "full",
} = {}) {
  if (!textarea?.ownerDocument || typeof controller?.execute !== "function") {
    throw new TypeError("slash palette requires a textarea and editor controller");
  }
  const profile = getEditorProfile(profileId);
  const allowed = profile.slashCommands.map(getCommand);
  const document = textarea.ownerDocument;
  const id = `markdown-slash-palette-${++paletteSequence}`;
  const palette = document.createElement("div");
  palette.id = id;
  palette.className = "markdown-slash-palette";
  palette.setAttribute("role", "listbox");
  palette.setAttribute("aria-label", "Markdown 명령");
  palette.hidden = true;
  document.body.append(palette);
  textarea.setAttribute("aria-controls", id);
  textarea.setAttribute("aria-expanded", "false");

  let activeIndex = 0;
  let context = null;
  let matches = [];
  let composing = false;
  let destroyed = false;
  let dismissedSignature = null;

  function close({ dismiss = false } = {}) {
    if (dismiss && context) {
      dismissedSignature = context.signature;
    }
    context = null;
    matches = [];
    activeIndex = 0;
    palette.hidden = true;
    palette.replaceChildren();
    textarea.setAttribute("aria-expanded", "false");
    textarea.removeAttribute("aria-activedescendant");
  }

  function renderOptions() {
    palette.replaceChildren();
    matches.forEach((command, index) => {
      const option = document.createElement("div");
      option.id = `${id}-option-${index}`;
      option.className = "markdown-slash-palette__option";
      option.setAttribute("role", "option");
      option.setAttribute("aria-selected", String(index === activeIndex));
      option.dataset.markdownCommand = command.id;
      const label = document.createElement("strong");
      label.textContent = command.label;
      const description = document.createElement("span");
      description.textContent = command.description;
      option.append(label, description);
      option.addEventListener("mousedown", (event) => {
        event.preventDefault();
        executeAt(index);
      });
      palette.append(option);
    });
    if (matches.length) {
      textarea.setAttribute(
        "aria-activedescendant",
        `${id}-option-${activeIndex}`,
      );
    } else {
      textarea.removeAttribute("aria-activedescendant");
    }
  }

  function update() {
    if (destroyed || composing || controller.isComposing()) {
      close();
      return;
    }
    const nextContext = slashContext(textarea);
    if (!nextContext || nextContext.signature === dismissedSignature) {
      close();
      return;
    }
    context = nextContext;
    matches = [...searchCommands(context.query, allowed)];
    activeIndex = Math.min(activeIndex, Math.max(0, matches.length - 1));
    palette.hidden = false;
    textarea.setAttribute("aria-expanded", "true");
    placePalette(palette, textarea);
    renderOptions();
  }

  function executeAt(index) {
    const command = matches[index];
    if (!command || !context) {
      return;
    }
    textarea.setRangeText("", context.start, context.end, "end");
    const view = document.defaultView;
    textarea.dispatchEvent(new view.InputEvent("input", {
      bubbles: true,
      composed: true,
      inputType: "deleteContentBackward",
    }));
    dismissedSignature = null;
    close();
    controller.execute(command.id);
  }

  function handleKeydown(event) {
    if (palette.hidden || composing || event.isComposing) {
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const delta = event.key === "ArrowDown" ? 1 : -1;
      activeIndex = matches.length
        ? (activeIndex + delta + matches.length) % matches.length
        : 0;
      renderOptions();
      return;
    }
    if ((event.key === "Enter" || event.key === "Tab") && matches.length) {
      event.preventDefault();
      executeAt(activeIndex);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      close({ dismiss: true });
    }
  }

  function handleCompositionStart() {
    composing = true;
    close();
  }

  function handleCompositionEnd() {
    composing = false;
    update();
  }

  textarea.addEventListener("input", update);
  textarea.addEventListener("click", update);
  textarea.addEventListener("keyup", update);
  textarea.addEventListener("keydown", handleKeydown);
  textarea.addEventListener("compositionstart", handleCompositionStart);
  textarea.addEventListener("compositionend", handleCompositionEnd);

  return Object.freeze({
    close,
    destroy() {
      if (destroyed) {
        return;
      }
      destroyed = true;
      textarea.removeEventListener("input", update);
      textarea.removeEventListener("click", update);
      textarea.removeEventListener("keyup", update);
      textarea.removeEventListener("keydown", handleKeydown);
      textarea.removeEventListener("compositionstart", handleCompositionStart);
      textarea.removeEventListener("compositionend", handleCompositionEnd);
      textarea.removeAttribute("aria-controls");
      textarea.removeAttribute("aria-expanded");
      textarea.removeAttribute("aria-activedescendant");
      palette.remove();
    },
    element: palette,
    update,
  });
}

