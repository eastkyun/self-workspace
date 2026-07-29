export function normalizeSelection(value, start, end) {
  const text = String(value ?? "");
  const safeStart = Math.max(0, Math.min(text.length, Number(start) || 0));
  const safeEnd = Math.max(
    safeStart,
    Math.min(text.length, Number(end) || safeStart),
  );
  return Object.freeze({
    value: text,
    start: safeStart,
    end: safeEnd,
    selected: text.slice(safeStart, safeEnd),
  });
}

export function replaceSelection(selection, replacement, {
  selectStart = replacement.length,
  selectEnd = selectStart,
} = {}) {
  const value = [
    selection.value.slice(0, selection.start),
    replacement,
    selection.value.slice(selection.end),
  ].join("");
  return Object.freeze({
    value,
    selectionStart: selection.start + selectStart,
    selectionEnd: selection.start + selectEnd,
  });
}

export function selectedLineRange(selection) {
  const lineStart = selection.value.lastIndexOf("\n", selection.start - 1) + 1;
  const nextLineBreak = selection.value.indexOf("\n", selection.end);
  const lineEnd = nextLineBreak === -1
    ? selection.value.length
    : nextLineBreak;
  return Object.freeze({
    ...selection,
    start: lineStart,
    end: lineEnd,
    selected: selection.value.slice(lineStart, lineEnd),
  });
}

