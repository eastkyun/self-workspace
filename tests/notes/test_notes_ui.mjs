import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createNotesApi } from "../../apps/notes/app/static/notes-api.js";
import {
  draftsEqual,
  emptyDraft,
  noteToDraft,
  noteTypeLabel,
  requestedNoteId,
  requestedNoteType,
  requestedSource,
  sortNotes,
} from "../../apps/notes/app/static/notes-state.js";

const template = await readFile(
  new URL("../../apps/notes/app/templates/notes.html", import.meta.url),
  "utf8",
);
const styles = await readFile(
  new URL("../../apps/notes/app/static/notes.css", import.meta.url),
  "utf8",
);
const script = await readFile(
  new URL("../../apps/notes/app/static/notes.js", import.meta.url),
  "utf8",
);

test("normalizes drafts and detects meaningful changes", () => {
  assert.deepEqual(emptyDraft("meeting_minutes"), {
    title: "",
    content: "",
    note_type: "meeting_minutes",
    is_pinned: false,
  });
  assert.equal(draftsEqual({ ...emptyDraft(), title: " note " }, { ...emptyDraft(), title: "note" }), true);
  assert.equal(draftsEqual(emptyDraft(), { ...emptyDraft(), content: "changed" }), false);
  assert.equal(noteToDraft({ title: "A", note_type: "invalid" }).note_type, "general");
});

test("sorts pinned and recently updated notes deterministically", () => {
  const notes = [
    { id: 1, is_pinned: false, updated_at: "2026-01-02T00:00:00Z" },
    { id: 2, is_pinned: true, updated_at: "2026-01-01T00:00:00Z" },
    { id: 3, is_pinned: true, updated_at: "2026-01-01T00:00:00Z" },
  ];
  assert.deepEqual(sortNotes(notes).map(({ id }) => id), [3, 2, 1]);
});

test("parses supported route state only", () => {
  assert.equal(requestedNoteId("?note_id=12"), 12);
  assert.equal(requestedNoteId("?note_id=12x"), null);
  assert.equal(requestedNoteType("?type=research_note"), "research_note");
  assert.equal(requestedNoteType("?type=other"), "general");
  assert.deepEqual(
    requestedSource("?source_app=calendar&source_type=event&source_id=13"),
    { source_app: "calendar", source_type: "event", source_id: "13" },
  );
  assert.equal(requestedSource("?source_app=calendar"), null);
  assert.equal(noteTypeLabel("meeting_minutes"), "회의록");
});

test("API adapter builds mounted relative CRUD requests", async () => {
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      status: options.method === "DELETE" ? 204 : 200,
      json: async () => [],
      arrayBuffer: async () => new ArrayBuffer(0),
    };
  };
  const api = createNotesApi("./api/notes", fetchImpl);
  await api.list({ query: "  hello  ", noteType: "general" });
  await api.create(emptyDraft());
  await api.update(3, emptyDraft("research_note"));
  await api.remove(3);

  assert.equal(calls[0].url, "./api/notes?q=hello&type=general");
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[2].url, "./api/notes/3");
  assert.equal(calls[2].options.method, "PUT");
  assert.equal(calls[3].options.method, "DELETE");
});

test("UI contains accessible list, editor, statuses, and dirty guard", () => {
  assert.match(script, /from "\/static\/shared\/markdown\/index\.js"/);
  assert.match(template, /role="listbox"/);
  assert.match(script, /setAttribute\("aria-selected"/);
  assert.match(template, /aria-live="polite"/);
  assert.match(template, /id="notes-back"/);
  assert.match(script, /beforeunload/);
  assert.match(script, /window\.confirm\("저장하지 않은 변경사항/);
  assert.match(script, /ArrowDown/);
  assert.match(script, /payload\.links = \[state\.pendingSource\]/);
  assert.match(template, /id="note-context"/);
  assert.match(template, /id="note-save-status"[^>]*aria-live="polite"/);
  assert.match(template, /id="note-view"[^>]*hidden/);
  assert.match(template, /id="edit-note"/);
  assert.match(script, /const viewOpen = Boolean\(state\.selectedNote && !state\.editing\)/);
  assert.match(script, /listRequest/);
  assert.match(script, /selectionRequest/);
  assert.match(script, /history\.go\(delta\)/);
  assert.match(script, /updateLocation\(null, \{ mode: "new" \}\)/);
  assert.match(script, /mutationRequest/);
  assert.match(script, /request !== state\.selectionRequest/);
  assert.match(script, /request !== state\.listRequest/);
  assert.match(script, /requestAnimationFrame\(\(\) => elements\.editButton\.focus\(\)\)/);
  assert.match(template, /static\/shared\/markdown\/markdown\.css/);
  assert.match(template, /id="note-markdown-toolbar"/);
  assert.match(template, /id="note-markdown-preview"/);
  assert.match(script, /profile: "full"/);
  assert.match(script, /mountMarkdownViewer/);
  assert.doesNotMatch(script, /viewContent\.textContent = state\.selectedNote\.content/);
  assert.match(script, /markdownRenderer\.extractExcerpt/);
  assert.match(template, /id="new-note"[^>]*disabled/);
  assert.match(script, /elements\.newButton\.disabled = false/);
});

test("mobile layout is single-column without horizontal overflow", () => {
  assert.match(styles, /\.notes-app \[hidden\]\s*\{\s*display:\s*none !important/);
  assert.match(styles, /@media \(max-width: 760px\)/);
  assert.match(styles, /\.notes-app\.is-editing \.notes-list-pane\s*\{\s*display:\s*none/);
  assert.match(styles, /\.notes-document\s*\{[^}]*grid-template-columns:\s*1fr/s);
  assert.match(styles, /\.notes-app\s*\{[^}]*min-width:\s*0/s);
  assert.match(styles, /\.notes-workspace\s*\{[^}]*min-width:\s*0/s);
});

test("signals readiness after the initial notes request settles", () => {
  assert.match(template, /data-workspace-await-ready/);
  assert.match(script, /\.finally\(\(\) => globalThis\.workspaceAppReady\?\.\(\)\)/);
});
