import { applyCommand, getCommand } from "./commands.js";
import { getEditorProfile } from "./profiles.js";

const ATTACHED_CONTROLLERS = new WeakMap();

const SHORTCUTS = Object.freeze([
  { key: "b", command: "bold" },
  { key: "i", command: "italic" },
  { key: "k", command: "link" },
  { key: "7", shift: true, command: "ordered-list" },
  { key: "8", shift: true, command: "unordered-list" },
  { key: "p", shift: true, command: "preview-toggle" },
]);

function applyTextareaChange(textarea, result) {
  const previous = textarea.value;
  let prefix = 0;
  while (prefix < previous.length
      && prefix < result.value.length
      && previous[prefix] === result.value[prefix]) {
    prefix += 1;
  }

  let oldSuffix = previous.length;
  let newSuffix = result.value.length;
  while (oldSuffix > prefix
      && newSuffix > prefix
      && previous[oldSuffix - 1] === result.value[newSuffix - 1]) {
    oldSuffix -= 1;
    newSuffix -= 1;
  }

  textarea.setRangeText(
    result.value.slice(prefix, newSuffix),
    prefix,
    oldSuffix,
    "end",
  );
  textarea.setSelectionRange(result.selectionStart, result.selectionEnd);
  textarea.dispatchEvent(new textarea.ownerDocument.defaultView.InputEvent(
    "input",
    {
      bubbles: true,
      composed: true,
      data: null,
      inputType: "insertText",
    },
  ));
}

function matchingShortcut(event) {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) {
    return null;
  }
  const key = event.key.toLocaleLowerCase();
  return SHORTCUTS.find((shortcut) => (
    shortcut.key === key
    && Boolean(shortcut.shift) === event.shiftKey
  )) ?? null;
}

export function attachMarkdownEditor(textarea, {
  profile: profileId = "full",
  onChange = () => {},
  onPreviewToggle = () => {},
} = {}) {
  if (!textarea
      || textarea.tagName?.toLocaleLowerCase() !== "textarea") {
    throw new TypeError("Markdown editor requires a textarea element");
  }
  if (ATTACHED_CONTROLLERS.has(textarea)) {
    throw new Error("Markdown editor is already attached to this textarea");
  }

  const profile = getEditorProfile(profileId);
  let composing = false;
  let destroyed = false;

  function assertActive() {
    if (destroyed) {
      throw new Error("Markdown editor controller has been destroyed");
    }
  }

  function execute(commandId) {
    assertActive();
    const command = getCommand(commandId);
    const allowed = new Set([
      ...profile.toolbarCommands,
      ...profile.slashCommands,
    ]);
    if (!allowed.has(commandId)) {
      throw new TypeError(
        `command ${commandId} is unavailable in profile ${profile.id}`,
      );
    }
    if (command.kind === "view") {
      onPreviewToggle();
      return null;
    }
    const result = applyCommand(
      commandId,
      textarea.value,
      textarea.selectionStart,
      textarea.selectionEnd,
    );
    applyTextareaChange(textarea, result);
    textarea.focus();
    return result;
  }

  function handleInput() {
    if (!destroyed && !composing) {
      onChange(textarea.value, {
        composing,
      });
    }
  }

  function handleKeydown(event) {
    if (destroyed || composing || event.isComposing) {
      return;
    }
    const shortcut = matchingShortcut(event);
    if (!shortcut) {
      return;
    }
    const allowed = profile.toolbarCommands.includes(shortcut.command)
      || profile.slashCommands.includes(shortcut.command);
    if (!allowed) {
      return;
    }
    event.preventDefault();
    execute(shortcut.command);
  }

  function handleCompositionStart() {
    composing = true;
  }

  function handleCompositionEnd() {
    composing = false;
    handleInput();
  }

  textarea.addEventListener("input", handleInput);
  textarea.addEventListener("keydown", handleKeydown);
  textarea.addEventListener("compositionstart", handleCompositionStart);
  textarea.addEventListener("compositionend", handleCompositionEnd);

  const controller = Object.freeze({
    destroy() {
      if (destroyed) {
        return;
      }
      destroyed = true;
      textarea.removeEventListener("input", handleInput);
      textarea.removeEventListener("keydown", handleKeydown);
      textarea.removeEventListener("compositionstart", handleCompositionStart);
      textarea.removeEventListener("compositionend", handleCompositionEnd);
      ATTACHED_CONTROLLERS.delete(textarea);
    },
    execute,
    focus() {
      assertActive();
      textarea.focus();
    },
    getSelection() {
      assertActive();
      return Object.freeze({
        start: textarea.selectionStart,
        end: textarea.selectionEnd,
      });
    },
    getValue() {
      assertActive();
      return textarea.value;
    },
    isComposing() {
      return composing;
    },
    setValue(value) {
      assertActive();
      textarea.value = String(value ?? "");
      textarea.dispatchEvent(new textarea.ownerDocument.defaultView.InputEvent(
        "input",
        { bubbles: true, composed: true, inputType: "insertText" },
      ));
    },
  });
  ATTACHED_CONTROLLERS.set(textarea, controller);
  return controller;
}
