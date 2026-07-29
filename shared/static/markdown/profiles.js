const FULL_TOOLBAR = Object.freeze([
  "heading1",
  "heading2",
  "heading3",
  "bold",
  "italic",
  "strikethrough",
  "unordered-list",
  "ordered-list",
  "task-list",
  "quote",
  "inline-code",
  "code-block",
  "link",
  "horizontal-rule",
  "table",
  "preview-toggle",
]);

const COMPACT_TOOLBAR = Object.freeze([
  "bold",
  "italic",
  "unordered-list",
  "task-list",
  "quote",
  "inline-code",
  "link",
  "preview-toggle",
]);

const COMPACT_SLASH = Object.freeze([
  ...COMPACT_TOOLBAR.filter((command) => command !== "preview-toggle"),
  "heading2",
  "heading3",
  "ordered-list",
  "code-block",
  "horizontal-rule",
  "table",
]);

const MINIMAL_COMMANDS = Object.freeze([
  "bold",
  "italic",
  "link",
  "unordered-list",
]);

export const EDITOR_PROFILES = Object.freeze({
  full: Object.freeze({
    id: "full",
    toolbarCommands: FULL_TOOLBAR,
    slashCommands: FULL_TOOLBAR.filter(
      (command) => command !== "preview-toggle",
    ),
    viewModes: Object.freeze(["editor", "split", "preview"]),
    headingAnchors: true,
    showToc: true,
    compact: false,
  }),
  compact: Object.freeze({
    id: "compact",
    toolbarCommands: COMPACT_TOOLBAR,
    slashCommands: COMPACT_SLASH,
    viewModes: Object.freeze(["editor", "preview"]),
    headingAnchors: false,
    showToc: false,
    compact: true,
  }),
  minimal: Object.freeze({
    id: "minimal",
    toolbarCommands: MINIMAL_COMMANDS,
    slashCommands: MINIMAL_COMMANDS,
    viewModes: Object.freeze(["editor"]),
    headingAnchors: false,
    showToc: false,
    compact: true,
  }),
});

export function getEditorProfile(profileId) {
  const profile = EDITOR_PROFILES[profileId];
  if (!profile) {
    throw new TypeError(`unknown editor profile: ${profileId}`);
  }
  return profile;
}

