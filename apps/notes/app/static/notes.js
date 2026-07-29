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
import {
  attachMarkdownEditor,
  attachMarkdownPreview,
  attachSlashPalette,
  collectInternalLinks,
  createMarkdownRenderer,
  createMarkdownToolbar,
  mountMarkdownViewer,
  resolveInternalLinks,
} from "./shared/markdown/index.js";

const root = document.querySelector("#notes-app");

if (root) {
  const api = createNotesApi(root.dataset.apiBase);
  const elements = {
    listPane: root.querySelector("#notes-list-pane"),
    list: root.querySelector("#notes-list"),
    listStatus: root.querySelector("#notes-list-status"),
    editorPane: root.querySelector("#notes-editor-pane"),
    empty: root.querySelector("#notes-empty"),
    emptyNewButton: root.querySelector("#empty-new-note"),
    view: root.querySelector("#note-view"),
    viewBackButton: root.querySelector("#view-notes-back"),
    editButton: root.querySelector("#edit-note"),
    viewTitle: root.querySelector("#note-view-title"),
    viewType: root.querySelector("#note-view-type"),
    viewUpdated: root.querySelector("#note-view-updated"),
    viewContent: root.querySelector("#note-view-content"),
    viewContext: root.querySelector("#view-note-context"),
    viewLinks: root.querySelector("#view-note-links"),
    form: root.querySelector("#note-form"),
    search: root.querySelector("#notes-search"),
    typeFilter: root.querySelector("#notes-type-filter"),
    newButton: root.querySelector("#new-note"),
    backButton: root.querySelector("#notes-back"),
    title: root.querySelector("#note-title"),
    content: root.querySelector("#note-content"),
    markdownToolbar: root.querySelector("#note-markdown-toolbar"),
    markdownPreview: root.querySelector("#note-markdown-preview"),
    noteType: root.querySelector("#note-type"),
    pin: root.querySelector("#note-pin"),
    delete: root.querySelector("#note-delete"),
    save: root.querySelector("#note-save"),
    error: root.querySelector("#notes-error"),
    announcer: root.querySelector("#notes-announcer"),
    saveStatus: root.querySelector("#note-save-status"),
    context: root.querySelector("#note-context"),
    contextTitle: root.querySelector("#note-context-title"),
    links: root.querySelector("#note-links"),
  };
  const markdownRenderer = createMarkdownRenderer();
  const markdownPreview = attachMarkdownPreview(
    elements.content,
    elements.markdownPreview,
    {
      renderer: markdownRenderer,
      mode: "editor",
      renderOptions: {
        showToc: true,
        emptyMessage: "미리볼 내용이 없습니다.",
      },
    },
  );
  const markdownEditor = attachMarkdownEditor(elements.content, {
    profile: "full",
    onPreviewToggle: () => markdownPreview.toggle(),
  });
  const markdownToolbar = createMarkdownToolbar({
    document,
    profile: "full",
    execute: (commandId) => markdownEditor.execute(commandId),
    ariaLabel: "메모 Markdown 서식",
  });
  elements.markdownToolbar.append(markdownToolbar);
  markdownPreview.setToggleButton(
    markdownToolbar.querySelector('[data-markdown-command="preview-toggle"]'),
  );
  attachSlashPalette(elements.content, markdownEditor, { profile: "full" });
  let noteViewer = null;
  let viewerRequest = 0;
  const state = {
    notes: [],
    selectedNote: null,
    baselineDraft: emptyDraft(),
    draft: emptyDraft(),
    creating: false,
    editing: false,
    loading: false,
    saving: false,
    pendingSource: requestedSource(window.location.search),
    links: [],
    listRequest: 0,
    selectionRequest: 0,
    linksRequest: 0,
    mutationRequest: 0,
    historyIndex: Number(window.history.state?.notesIndex ?? 0),
    restoringHistory: false,
  };
  window.history.replaceState(
    { ...window.history.state, notesIndex: state.historyIndex },
    "",
    window.location.href,
  );

  function isDirty() {
    return !draftsEqual(state.draft, state.baselineDraft);
  }

  function announce(message) {
    elements.announcer.textContent = message;
    elements.saveStatus.textContent = message;
  }

  function showError(error) {
    elements.error.textContent = error.message || "오류가 발생했습니다.";
    elements.error.hidden = false;
  }

  function clearError() {
    elements.error.hidden = true;
    elements.error.textContent = "";
  }

  function updateLocation(
    noteId = null,
    { replace = false, mode = "list" } = {},
  ) {
    const url = new URL(window.location.href);
    const basePath = url.pathname.replace(/\/new\/?$/, "/");
    url.pathname = mode === "new"
      ? `${basePath.replace(/\/$/, "")}/new`
      : basePath;
    if (noteId) url.searchParams.set("note_id", noteId);
    else url.searchParams.delete("note_id");
    url.searchParams.delete("type");
    url.searchParams.delete("source_app");
    url.searchParams.delete("source_type");
    url.searchParams.delete("source_id");
    const method = replace ? "replaceState" : "pushState";
    if (!replace) state.historyIndex += 1;
    window.history[method](
      { ...window.history.state, notesIndex: state.historyIndex },
      "",
      url,
    );
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
    elements.saveStatus.textContent = isDirty() ? "저장되지 않음" : "";
  }

  function renderEditor() {
    const formOpen = state.creating || state.editing;
    const viewOpen = Boolean(state.selectedNote && !state.editing);
    const detailOpen = formOpen || viewOpen;
    elements.empty.hidden = detailOpen;
    elements.form.hidden = !formOpen;
    elements.view.hidden = !viewOpen;
    root.classList.toggle("is-editing", detailOpen);
    if (viewOpen) renderView();
    if (!formOpen) return;

    elements.title.value = state.draft.title;
    elements.content.value = state.draft.content;
    elements.noteType.value = state.draft.note_type;
    elements.pin.setAttribute("aria-pressed", String(state.draft.is_pinned));
    elements.pin.textContent = state.draft.is_pinned ? "고정 해제" : "고정";
    elements.delete.hidden = state.creating;
    elements.save.disabled = state.saving;
    elements.save.textContent = state.saving ? "저장 중…" : "저장";
    if (state.saving) elements.saveStatus.textContent = "저장 중…";
    markdownPreview.refresh();
    renderLinks();
  }

  async function renderView() {
    if (!state.selectedNote) return;
    const request = ++viewerRequest;
    elements.viewTitle.textContent = state.selectedNote.title;
    elements.viewType.textContent = noteTypeLabel(state.selectedNote.note_type);
    elements.viewUpdated.textContent = `${new Date(state.selectedNote.updated_at).toLocaleString("ko-KR")} 수정`;
    elements.viewUpdated.dateTime = state.selectedNote.updated_at;
    const source = state.selectedNote.content;
    const references = collectInternalLinks(source);
    let internalLinkResolutions = new Map();
    if (references.length) {
      try {
        internalLinkResolutions = await resolveInternalLinks(references, {
          endpoint: root.dataset.internalLinkEndpoint,
        });
      } catch (error) {
        if (request === viewerRequest) showError(error);
      }
    }
    if (request !== viewerRequest || state.selectedNote?.content !== source) return;
    noteViewer?.destroy();
    noteViewer = mountMarkdownViewer(elements.viewContent, source, {
      renderer: markdownRenderer,
      renderOptions: {
        showToc: true,
        internalLinkResolutions,
        emptyMessage: "내용이 없습니다.",
      },
      ariaLabel: `${state.selectedNote.title} 본문`,
    });
    renderLinks();
  }

  function createLinkCard(link, { editable }) {
    const card = document.createElement("div");
    card.className = `note-link-card${link.orphan ? " is-orphan" : ""}`;
    const label = document.createElement("span");
    label.textContent = link.orphan
      ? `${link.source_title_snapshot} · 삭제된 일정`
      : link.source_title_snapshot;
    card.append(label);
    if (!editable) return card;
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
    card.append(unlink);
    return card;
  }

  function renderLinks() {
    const hasContext = Boolean(state.pendingSource || state.links.length);
    elements.context.hidden = !hasContext;
    elements.viewContext.hidden = !state.links.length;
    elements.links.replaceChildren();
    elements.viewLinks.replaceChildren();
    if (state.pendingSource) {
      const pending = document.createElement("span");
      pending.className = "note-link-card";
      pending.textContent = `Calendar 일정 #${state.pendingSource.source_id} · 저장 시 연결`;
      elements.links.append(pending);
    }
    for (const link of state.links) {
      elements.links.append(createLinkCard(link, { editable: true }));
      elements.viewLinks.append(createLinkCard(link, { editable: false }));
    }
  }

  async function loadLinks(noteId) {
    const request = ++state.linksRequest;
    const links = await api.links(noteId);
    if (request !== state.linksRequest || state.selectedNote?.id !== noteId) return;
    state.links = links;
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
    metadata.className = "notes-list-metadata";
    const badge = document.createElement("span");
    badge.className = "notes-type-badge";
    badge.textContent = noteTypeLabel(note.note_type);
    const updated = document.createElement("span");
    updated.textContent = new Date(note.updated_at).toLocaleDateString("ko-KR");
    metadata.append(badge, updated);
    if (note.is_pinned) {
      const pin = document.createElement("span");
      pin.className = "notes-pin-indicator";
      pin.textContent = "고정";
      metadata.prepend(pin);
    }
    const preview = document.createElement("span");
    preview.className = "notes-list-preview";
    preview.textContent = note.content
      ? markdownRenderer.extractExcerpt(note.content, 140)
      : "내용 없음";
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
    {
      updateHistory = true,
      preserveSource = false,
      confirm = true,
    } = {},
  ) {
    if (confirm && !confirmDiscard()) return;
    state.selectionRequest += 1;
    state.linksRequest += 1;
    state.mutationRequest += 1;
    state.saving = false;
    state.selectedNote = null;
    state.creating = true;
    state.editing = false;
    state.links = [];
    if (!preserveSource) state.pendingSource = null;
    setDraft(emptyDraft(noteType));
    renderList();
    if (updateHistory) updateLocation(null, { mode: "new" });
    window.requestAnimationFrame(() => elements.title.focus());
  }

  async function selectNote(
    noteId,
    { updateHistory = true, confirm = true } = {},
  ) {
    if (confirm && !confirmDiscard()) return;
    state.mutationRequest += 1;
    state.saving = false;
    const request = ++state.selectionRequest;
    clearError();
    try {
      const note = await api.get(noteId);
      const links = await api.links(note.id);
      if (request !== state.selectionRequest) return;
      state.linksRequest += 1;
      state.links = links;
      state.selectedNote = note;
      state.creating = false;
      state.editing = false;
      state.pendingSource = null;
      setDraft(note);
      renderList();
      if (updateHistory) updateLocation(note.id);
      window.requestAnimationFrame(() => elements.editButton.focus());
    } catch (error) {
      if (request !== state.selectionRequest) return;
      showError(error);
    }
  }

  async function loadNotes({ selectRequested = false } = {}) {
    const request = ++state.listRequest;
    state.loading = true;
    renderList();
    clearError();
    try {
      const notes = await api.list({
        query: elements.search.value,
        noteType: elements.typeFilter.value,
      });
      if (request !== state.listRequest) return;
      state.notes = notes;
      renderList();
      if (selectRequested) {
        const noteId = requestedNoteId(window.location.search);
        if (noteId) await selectNote(noteId, { updateHistory: false });
      }
    } catch (error) {
      if (request !== state.listRequest) return;
      showError(error);
    } finally {
      if (request !== state.listRequest) return;
      state.loading = false;
      renderList();
    }
  }

  async function saveNote(event) {
    event.preventDefault();
    readDraft();
    clearError();
    state.saving = true;
    const request = ++state.mutationRequest;
    renderEditor();
    try {
      const payload = normalizedDraft(state.draft);
      if (state.creating && state.pendingSource) {
        payload.links = [state.pendingSource];
      }
      const note = state.creating
        ? await api.create(payload)
        : await api.update(state.selectedNote.id, payload);
      if (request !== state.mutationRequest) return;
      state.selectedNote = note;
      state.creating = false;
      state.editing = false;
      state.pendingSource = null;
      await loadLinks(note.id);
      if (request !== state.mutationRequest) return;
      setDraft(note);
      updateLocation(note.id, { replace: true });
      await loadNotes();
      if (request !== state.mutationRequest) return;
      announce("메모를 저장했습니다.");
      window.requestAnimationFrame(() => elements.editButton.focus());
    } catch (error) {
      if (request !== state.mutationRequest) return;
      showError(error);
    } finally {
      if (request === state.mutationRequest) {
        state.saving = false;
        renderEditor();
      }
    }
  }

  async function deleteNote() {
    if (!state.selectedNote || !window.confirm("이 메모를 삭제할까요?")) return;
    clearError();
    const request = ++state.mutationRequest;
    try {
      await api.remove(state.selectedNote.id);
      if (request !== state.mutationRequest) return;
      state.selectedNote = null;
      state.creating = false;
      state.editing = false;
      state.links = [];
      setDraft(emptyDraft());
      updateLocation();
      await loadNotes();
      if (request !== state.mutationRequest) return;
      announce("메모를 삭제했습니다.");
      window.requestAnimationFrame(() => elements.search.focus());
    } catch (error) {
      if (request !== state.mutationRequest) return;
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
  elements.emptyNewButton.addEventListener("click", () => startNew());
  function returnToList() {
    if (!confirmDiscard()) return;
    state.selectionRequest += 1;
    state.linksRequest += 1;
    state.mutationRequest += 1;
    state.saving = false;
    state.selectedNote = null;
    state.creating = false;
    state.editing = false;
    state.pendingSource = null;
    state.links = [];
    setDraft(emptyDraft());
    updateLocation();
    window.requestAnimationFrame(() => elements.search.focus());
  }

  elements.backButton.addEventListener("click", returnToList);
  elements.viewBackButton.addEventListener("click", returnToList);
  elements.editButton.addEventListener("click", () => {
    if (!state.selectedNote) return;
    state.editing = true;
    setDraft(state.selectedNote);
    window.requestAnimationFrame(() => elements.title.focus());
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
    const targetIndex = Number(window.history.state?.notesIndex ?? 0);
    if (state.restoringHistory) {
      state.restoringHistory = false;
      return;
    }
    if (!confirmDiscard()) {
      const delta = state.historyIndex - targetIndex;
      if (delta) {
        state.restoringHistory = true;
        window.history.go(delta);
      }
      return;
    }
    state.historyIndex = targetIndex;
    state.selectionRequest += 1;
    state.linksRequest += 1;
    state.mutationRequest += 1;
    state.saving = false;
    const noteId = requestedNoteId(window.location.search);
    if (noteId) {
      selectNote(noteId, { updateHistory: false, confirm: false });
    }
    else if (window.location.pathname.endsWith("/new")) {
      state.pendingSource = requestedSource(window.location.search);
      startNew(requestedNoteType(window.location.search), {
        updateHistory: false,
        preserveSource: true,
        confirm: false,
      });
    } else {
      state.selectedNote = null;
      state.creating = false;
      state.editing = false;
      state.pendingSource = null;
      state.links = [];
      setDraft(emptyDraft());
      renderList();
      window.requestAnimationFrame(() => elements.search.focus());
    }
  });

  elements.newButton.disabled = false;
  root.dataset.ready = "true";
  loadNotes({ selectRequested: true }).then(() => {
    if (root.dataset.mode === "new") {
      startNew(requestedNoteType(window.location.search), {
        updateHistory: false,
        preserveSource: true,
        confirm: false,
      });
    }
  });
}
