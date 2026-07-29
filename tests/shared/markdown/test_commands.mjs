import assert from "node:assert/strict";
import test from "node:test";

import {
  COMMANDS,
  applyCommand,
  getCommand,
  searchCommands,
} from "../../../shared/static/markdown/commands.js";

test("command registry is the single immutable toolbar and slash source", () => {
  assert.ok(Object.isFrozen(COMMANDS));
  assert.equal(new Set(COMMANDS.map(({ id }) => id)).size, COMMANDS.length);
  assert.equal(getCommand("bold").toolbarVisible, true);
  assert.equal(getCommand("bold").slashVisible, true);
  assert.equal(getCommand("preview-toggle").kind, "view");
});

test("inline commands wrap selected Korean text and preserve selection", () => {
  assert.deepEqual(applyCommand("bold", "한글 텍스트", 0, 2), {
    value: "**한글** 텍스트",
    selectionStart: 2,
    selectionEnd: 4,
  });
  assert.deepEqual(applyCommand("italic", "", 0), {
    value: "**",
    selectionStart: 1,
    selectionEnd: 1,
  });
});

test("headings replace an existing prefix instead of duplicating it", () => {
  assert.deepEqual(applyCommand("heading2", "### 설치 방법", 0, 9), {
    value: "## 설치 방법",
    selectionStart: 3,
    selectionEnd: 8,
  });
});

test("list commands transform multiline selections", () => {
  assert.deepEqual(
    applyCommand("unordered-list", "사과\n배\n포도", 0, 7).value,
    "- 사과\n- 배\n- 포도",
  );
  assert.deepEqual(
    applyCommand("ordered-list", "사과\n배", 0, 4).value,
    "1. 사과\n2. 배",
  );
});

test("code block, link, rule and table commands place useful cursors", () => {
  const code = applyCommand("code-block", "print('안녕')", 0, 11);
  assert.match(code.value, /^```text\n/);
  assert.equal(code.value.endsWith("\n```"), true);

  const link = applyCommand("link", "문서", 0, 2);
  assert.equal(link.value, "[문서](https://)");
  assert.equal(
    link.value.slice(link.selectionStart, link.selectionEnd),
    "https://",
  );

  assert.equal(applyCommand("horizontal-rule", "앞", 1).value, "앞\n---");
  assert.match(applyCommand("table", "", 0).value, /^\| 제목 \| 제목 \|/);
});

test("command search normalizes case and whitespace in Korean and English", () => {
  assert.deepEqual(searchCommands("  BoLd  ").map(({ id }) => id), ["bold"]);
  assert.deepEqual(searchCommands("제목").map(({ id }) => id), [
    "heading1",
    "heading2",
    "heading3",
  ]);
  assert.equal(searchCommands("").length, COMMANDS.length);
});

