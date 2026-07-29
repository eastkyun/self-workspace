export const NOTE_TYPES = new Set([
  "general",
  "meeting_minutes",
  "research_note",
]);

export function emptyDraft(noteType = "general") {
  return {
    title: "",
    content: "",
    note_type: NOTE_TYPES.has(noteType) ? noteType : "general",
    is_pinned: false,
  };
}

export function noteToDraft(note) {
  return {
    title: note?.title ?? "",
    content: note?.content ?? "",
    note_type: NOTE_TYPES.has(note?.note_type) ? note.note_type : "general",
    is_pinned: Boolean(note?.is_pinned),
  };
}

export function normalizedDraft(draft) {
  return {
    title: String(draft.title ?? "").trim(),
    content: String(draft.content ?? ""),
    note_type: NOTE_TYPES.has(draft.note_type) ? draft.note_type : "general",
    is_pinned: Boolean(draft.is_pinned),
  };
}

export function draftsEqual(left, right) {
  return JSON.stringify(normalizedDraft(left)) === JSON.stringify(normalizedDraft(right));
}

export function sortNotes(notes) {
  return [...notes].sort((left, right) => (
    Number(right.is_pinned) - Number(left.is_pinned)
    || String(right.updated_at).localeCompare(String(left.updated_at))
    || right.id - left.id
  ));
}

export function noteTypeLabel(noteType) {
  return {
    general: "일반 메모",
    meeting_minutes: "회의록",
    research_note: "연구 노트",
  }[noteType] ?? "일반 메모";
}

export function requestedNoteId(search) {
  const raw = new URLSearchParams(search).get("note_id");
  if (raw === null || !/^[1-9]\d*$/.test(raw)) return null;
  return Number(raw);
}

export function requestedNoteType(search) {
  const value = new URLSearchParams(search).get("type");
  return NOTE_TYPES.has(value) ? value : "general";
}

export function requestedSource(search) {
  const params = new URLSearchParams(search);
  const source = {
    source_app: params.get("source_app"),
    source_type: params.get("source_type"),
    source_id: params.get("source_id"),
  };
  if (Object.values(source).every(Boolean)) return source;
  return null;
}
