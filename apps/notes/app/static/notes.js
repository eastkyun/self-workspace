import { createNotesApi } from "./notes-api.js";
import {
  draftsEqual,
  emptyDraft,
  normalizedDraft,
  noteToDraft,
  noteTypeLabel,
  requestedNoteId,
  requestedNoteType,
  requestedSource,
  sortNotes,
} from "./notes-state.js";

const root = document.querySelector("#notes-app");

if (root) {
  const api = createNotesApi(root.dataset.apiBase);
  const elements = {
    listPane: root.querySelector("#notes-list-pane"),
    list: root.querySelector("#notes-list"),
    listStatus: root.querySelector("#notes-list-status"),
    editorPane: root.querySelector("#notes-editor-pane"),
    empty: root.querySelector("#notes-empty"),
    form: root.querySelector("#note-form"),
    search: root.querySelector("#notes-search"),
    typeFilter: root.querySelector("#notes-type-filter"),
    newButton: root.querySelector("#new-note"),
    backButton: root.querySelector("#notes-back"),
    title: root.querySelector("#note-title"),
    content: root.querySelector("#note-content"),
    noteType: root.querySelector("#note-type"),
    pin: root.querySelector("#note-pin"),
    delete: root.querySelector("#note-delete"),
    save: root.querySelector("#note-save"),
    error: root.querySelector("#notes-error"),
    announcer: root.querySelector("#notes-announcer"),
    context: root.querySelector("#note-context"),
    contextTitle: root.querySelector("#note-context-title"),
    links: root.querySelector("#note-links"),
  };
  const state = {
    notes: [],
    selectedNote: null,
    baselineDraft: emptyDraft(),
    draft: emptyDraft(),
    creating: false,
    loading: false,
    saving: false,
    pendingSource: requestedSource(window.location.search),
    links: [],
  };

  function isDirty() {
    return !draftsEqual(state.draft, state.baselineDraft);
  }

  function announce(message) {
    elements.announcer.textContent = message;
  }

  function showError(error) {
    elements.error.textContent = error.message || "오류가 발생했습니다.";
    elements.error.hidden = false;
  }

  function clearError() {
    elements.error.hidden = true;
    elements.error.textContent = "";
  }

  function updateLocation(noteId = null, { replace = false } = {}) {
    const url = new URL(window.location.href);
    url.pathname = url.pathname.replace(/\/new\/?$/, "/");
    if (noteId) url.searchParams.set("note_id", noteId);
    else url.searchParams.delete("note_id");
    url.searchParams.delete("type");
    const method = replace ? "replaceState" : "pushState";
    window.history[method]({}, "", url);
  }

  function confirmDiscard() {
    return !isDirty() || window.confirm("저장하지 않은 변경사항을 버릴까요?");
  }

  function readDraft() {
    state.draft = {
      title: elements.title.value,
      content: elements.content.value,
      note_type: elements.noteType.value,
      is_pinned: elements.pin.getAttribute("aria-pressed") === "true",
    };
  }

  function renderEditor() {
    const open = state.creating || state.selectedNote;
    elements.empty.hidden = Boolean(open);
    elements.form.hidden = !open;
    root.classList.toggle("is-editing", Boolean(open));
    if (!open) return;

    elements.title.value = state.draft.title;
    elements.content.value = state.draft.content;
    elements.noteType.value = state.draft.note_type;
    elements.pin.setAttribute("aria-pressed", String(state.draft.is_pinned));
    elements.pin.textContent = state.draft.is_pinned ? "고정 해제" : "고정";
    elements.delete.hidden = state.creating;
    elements.save.disabled = state.saving;
    elements.save.textContent = state.saving ? "저장 중…" : "저장";
    renderLinks();
  }

  function renderLinks() {
    const hasContext = Boolean(state.pendingSource || state.links.length);
    elements.context.hidden = !hasContext;
    elements.links.replaceChildren();
    if (state.pendingSource) {
      const pending = document.createElement("span");
      pending.className = "note-link-card";
      pending.textContent = `Calendar 일정 #${state.pendingSource.source_id} · 저장 시 연결`;
      elements.links.append(pending);
    }
    for (const link of state.links) {
      const card = document.createElement("div");
      card.className = `note-link-card${link.orphan ? " is-orphan" : ""}`;
      const label = document.createElement("span");
      label.textContent = link.orphan
        ? `${link.source_title_snapshot} · 삭제된 일정`
        : link.source_title_snapshot;
      const unlink = document.createElement("button");
      unlink.type = "button";
      unlink.className = "notes-secondary-button";
      unlink.textContent = "연결 해제";
      unlink.setAttribute("aria-label", `${link.source_title_snapshot} 연결 해제`);
      unlink.addEventListener("click", async () => {
        try {
          await api.unlink(state.selectedNote.id, link.id);
          await loadLinks(state.selectedNote.id);
          announce("일정 연결을 해제했습니다.");
        } catch (error) {
          showError(error);
        }
      });
      card.append(label, unlink);
      elements.links.append(card);
    }
  }

  async function loadLinks(noteId) {
    state.links = await api.links(noteId);
    renderLinks();
  }

  function noteItem(note) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "notes-list-item";
    button.dataset.noteId = String(note.id);
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", String(state.selectedNote?.id === note.id));
    const title = document.createElement("strong");
    title.textContent = note.title;
    const metadata = document.createElement("span");
    metadata.textContent = `${note.is_pinned ? "★ " : ""}${noteTypeLabel(note.note_type)} · ${new Date(note.updated_at).toLocaleString("ko-KR")}`;
    const preview = document.createElement("span");
    preview.className = "notes-list-preview";
    preview.textContent = note.content || "내용 없음";
    button.append(title, metadata, preview);
    button.addEventListener("click", () => selectNote(note.id));
    return button;
  }

  function renderList() {
    elements.list.replaceChildren(...sortNotes(state.notes).map(noteItem));
    if (state.loading) elements.listStatus.textContent = "메모를 불러오는 중입니다.";
    else if (!state.notes.length && (elements.search.value || elements.typeFilter.value)) {
      elements.listStatus.textContent = "검색 결과가 없습니다.";
    } else if (!state.notes.length) elements.listStatus.textContent = "아직 작성한 메모가 없습니다.";
    else elements.listStatus.textContent = `${state.notes.length}개의 메모`;
  }

  function setDraft(draft, { baseline = draft } = {}) {
    state.draft = noteToDraft(draft);
    state.baselineDraft = noteToDraft(baseline);
    renderEditor();
  }

  function startNew(
    noteType = "general",
    { updateHistory = true, preserveSource = false } = {},
  ) {
    if (!confirmDiscard()) return;
    state.selectedNote = null;
    state.creating = true;
    state.links = [];
    if (!preserveSource) state.pendingSource = null;
    setDraft(emptyDraft(noteType));
    renderList();
    if (updateHistory) updateLocation();
    window.requestAnimationFrame(() => elements.title.focus());
  }

  async function selectNote(noteId, { updateHistory = true } = {}) {
    if (!confirmDiscard()) return;
    clearError();
    try {
      const note = await api.get(noteId);
      state.selectedNote = note;
      state.creating = false;
      state.pendingSource = null;
      await loadLinks(note.id);
      setDraft(note);
      renderList();
      if (updateHistory) updateLocation(note.id);
      window.requestAnimationFrame(() => elements.title.focus());
    } catch (error) {
      showError(error);
    }
  }

  async function loadNotes({ selectRequested = false } = {}) {
    state.loading = true;
    renderList();
    clearError();
    try {
      state.notes = await api.list({
        query: elements.search.value,
        noteType: elements.typeFilter.value,
      });
      renderList();
      if (selectRequested) {
        const noteId = requestedNoteId(window.location.search);
        if (noteId) await selectNote(noteId, { updateHistory: false });
      }
    } catch (error) {
      showError(error);
    } finally {
      state.loading = false;
      renderList();
    }
  }

  async function saveNote(event) {
    event.preventDefault();
    readDraft();
    clearError();
    state.saving = true;
    renderEditor();
    try {
      const payload = normalizedDraft(state.draft);
      if (state.creating && state.pendingSource) {
        payload.links = [state.pendingSource];
      }
      const note = state.creating
        ? await api.create(payload)
        : await api.update(state.selectedNote.id, payload);
      state.selectedNote = note;
      state.creating = false;
      state.pendingSource = null;
      await loadLinks(note.id);
      setDraft(note);
      updateLocation(note.id, { replace: true });
      await loadNotes();
      announce("메모를 저장했습니다.");
    } catch (error) {
      showError(error);
    } finally {
      state.saving = false;
      renderEditor();
    }
  }

  async function deleteNote() {
    if (!state.selectedNote || !window.confirm("이 메모를 삭제할까요?")) return;
    clearError();
    try {
      await api.remove(state.selectedNote.id);
      state.selectedNote = null;
      state.creating = false;
      state.links = [];
      setDraft(emptyDraft());
      updateLocation();
      await loadNotes();
      announce("메모를 삭제했습니다.");
    } catch (error) {
      showError(error);
    }
  }

  let searchTimer = null;
  elements.search.addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(() => loadNotes(), 180);
  });
  elements.typeFilter.addEventListener("change", () => loadNotes());
  elements.newButton.addEventListener("click", () => startNew());
  elements.backButton.addEventListener("click", () => {
    if (!confirmDiscard()) return;
    state.selectedNote = null;
    state.creating = false;
    state.pendingSource = null;
    state.links = [];
    setDraft(emptyDraft());
    updateLocation();
    elements.listPane.focus({ preventScroll: true });
  });
  elements.form.addEventListener("input", readDraft);
  elements.form.addEventListener("submit", saveNote);
  elements.delete.addEventListener("click", deleteNote);
  elements.pin.addEventListener("click", () => {
    readDraft();
    state.draft.is_pinned = !state.draft.is_pinned;
    renderEditor();
  });
  elements.list.addEventListener("keydown", (event) => {
    if (!["ArrowDown", "ArrowUp"].includes(event.key)) return;
    const items = [...elements.list.querySelectorAll(".notes-list-item")];
    const index = items.indexOf(document.activeElement);
    const next = event.key === "ArrowDown" ? index + 1 : index - 1;
    if (items[next]) {
      event.preventDefault();
      items[next].focus();
    }
  });
  window.addEventListener("beforeunload", (event) => {
    if (!isDirty()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("popstate", () => {
    if (!confirmDiscard()) {
      updateLocation(state.selectedNote?.id, { replace: true });
      return;
    }
    const noteId = requestedNoteId(window.location.search);
    if (noteId) selectNote(noteId, { updateHistory: false });
    else if (window.location.pathname.endsWith("/new")) {
      state.pendingSource = requestedSource(window.location.search);
      startNew(requestedNoteType(window.location.search), {
        updateHistory: false,
        preserveSource: true,
      });
    }
  });

  root.dataset.ready = "true";
  loadNotes({ selectRequested: true }).then(() => {
    if (root.dataset.mode === "new") {
      startNew(requestedNoteType(window.location.search), {
        updateHistory: false,
        preserveSource: true,
      });
    }
  });
}
