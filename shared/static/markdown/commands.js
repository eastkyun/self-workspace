import {
  normalizeSelection,
  replaceSelection,
  selectedLineRange,
} from "./selection.js";

function inlineWrap(selection, before, after = before, placeholder = "") {
  const content = selection.selected || placeholder;
  const replacement = `${before}${content}${after}`;
  if (selection.selected) {
    return replaceSelection(selection, replacement, {
      selectStart: before.length,
      selectEnd: before.length + content.length,
    });
  }
  return replaceSelection(selection, replacement, {
    selectStart: before.length,
    selectEnd: before.length + content.length,
  });
}

function prefixLines(selection, prefixFactory) {
  const lines = selectedLineRange(selection);
  const replacement = lines.selected
    .split("\n")
    .map((line, index) => `${prefixFactory(index)}${line}`)
    .join("\n");
  return replaceSelection(lines, replacement, {
    selectStart: 0,
    selectEnd: replacement.length,
  });
}

function heading(selection, level) {
  const lines = selectedLineRange(selection);
  const prefix = `${"#".repeat(level)} `;
  const replacement = lines.selected
    .split("\n")
    .map((line) => `${prefix}${line.replace(/^#{1,6}\s+/u, "")}`)
    .join("\n");
  return replaceSelection(lines, replacement, {
    selectStart: prefix.length,
    selectEnd: replacement.length,
  });
}

function fencedBlock(selection) {
  const content = selection.selected || "";
  const replacement = `\`\`\`text\n${content}\n\`\`\``;
  return replaceSelection(selection, replacement, {
    selectStart: 3,
    selectEnd: 7,
  });
}

function link(selection) {
  const label = selection.selected || "링크 텍스트";
  const replacement = `[${label}](https://)`;
  const hrefStart = label.length + 3;
  return replaceSelection(selection, replacement, {
    selectStart: hrefStart,
    selectEnd: hrefStart + "https://".length,
  });
}

function insertBlock(selection, block, cursorOffset = block.length) {
  const leading = selection.start > 0
    && selection.value.at(selection.start - 1) !== "\n"
    ? "\n"
    : "";
  const trailing = selection.end < selection.value.length
    && selection.value.at(selection.end) !== "\n"
    ? "\n"
    : "";
  return replaceSelection(selection, `${leading}${block}${trailing}`, {
    selectStart: leading.length + cursorOffset,
  });
}

const DEFINITIONS = [
  ["heading1", "제목 1", ["heading", "h1", "제목"], "Ctrl+Alt+1",
    (selection) => heading(selection, 1)],
  ["heading2", "제목 2", ["heading", "h2", "제목"], "Ctrl+Alt+2",
    (selection) => heading(selection, 2)],
  ["heading3", "제목 3", ["heading", "h3", "제목"], "Ctrl+Alt+3",
    (selection) => heading(selection, 3)],
  ["bold", "굵게", ["bold", "굵게"], "Ctrl+B",
    (selection) => inlineWrap(selection, "**")],
  ["italic", "기울임", ["italic", "기울임"], "Ctrl+I",
    (selection) => inlineWrap(selection, "*")],
  ["strikethrough", "취소선", ["strike", "취소선"], null,
    (selection) => inlineWrap(selection, "~~")],
  ["unordered-list", "글머리 목록", ["list", "목록", "bullet"], "Ctrl+Shift+8",
    (selection) => prefixLines(selection, () => "- ")],
  ["ordered-list", "번호 목록", ["list", "목록", "number"], "Ctrl+Shift+7",
    (selection) => prefixLines(selection, (index) => `${index + 1}. `)],
  ["task-list", "할 일 목록", ["task", "todo", "할 일"], null,
    (selection) => prefixLines(selection, () => "- [ ] ")],
  ["quote", "인용문", ["quote", "인용"], null,
    (selection) => prefixLines(selection, () => "> ")],
  ["inline-code", "인라인 코드", ["code", "코드"], null,
    (selection) => inlineWrap(selection, "`")],
  ["code-block", "코드 블록", ["code", "코드", "block"], null, fencedBlock],
  ["link", "링크", ["link", "링크"], "Ctrl+K", link],
  ["horizontal-rule", "구분선", ["rule", "divider", "구분선"], null,
    (selection) => insertBlock(selection, "---")],
  ["table", "표", ["table", "표"], null,
    (selection) => insertBlock(
      selection,
      "| 제목 | 제목 |\n| --- | --- |\n| 내용 | 내용 |",
      2,
    )],
  ["preview-toggle", "미리보기", ["preview", "미리보기"], "Ctrl+Shift+P", null],
];

export const COMMANDS = Object.freeze(DEFINITIONS.map(
  ([id, label, aliases, shortcut, transform]) => Object.freeze({
    id,
    label,
    description: `${label} Markdown 문법을 적용합니다.`,
    aliases: Object.freeze([...aliases]),
    shortcut,
    toolbarVisible: true,
    slashVisible: id !== "preview-toggle",
    kind: transform ? "edit" : "view",
    transform,
  }),
));

const COMMAND_BY_ID = new Map(COMMANDS.map((command) => [command.id, command]));

export function getCommand(commandId) {
  const command = COMMAND_BY_ID.get(commandId);
  if (!command) {
    throw new TypeError(`unknown Markdown command: ${commandId}`);
  }
  return command;
}

export function applyCommand(commandId, value, start, end = start) {
  const command = getCommand(commandId);
  if (command.kind !== "edit") {
    throw new TypeError(`command does not edit Markdown: ${commandId}`);
  }
  return command.transform(normalizeSelection(value, start, end));
}

export function searchCommands(query, commands = COMMANDS) {
  const normalized = String(query ?? "")
    .normalize("NFKC")
    .trim()
    .replace(/\s+/gu, " ")
    .toLocaleLowerCase();
  if (!normalized) {
    return Object.freeze([...commands]);
  }
  return Object.freeze(commands.filter((command) => [
    command.label,
    command.description,
    ...command.aliases,
  ].some((candidate) => candidate.toLocaleLowerCase().includes(normalized))));
}

