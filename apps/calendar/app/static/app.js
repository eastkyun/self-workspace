import {
  Calendar,
  classicThemePlugin,
  dayGridPlugin,
  koLocale,
} from "./vendor/fullcalendar/7.0.2/daygrid.esm.js";
import {
  activeTopicOptions,
  allDayInputToRange,
  apiTimestampToLocalInput,
  calendarDeepLinkEventId,
  filterTasks,
  filterTasksByTopic,
  formatTaskTime,
  isTaskOverdue,
  localDateTimeToKst,
  normalizeSelectedTopicId,
  paginateItems,
  sortTopics,
  taskToCalendarEvent,
  tasksForDate,
  topicCountsForTasks,
} from "./calendar-adapter.js";
import {
  attachMarkdownEditor,
  attachMarkdownPreview,
  attachSlashPalette,
  collectInternalLinks,
  createMarkdownRenderer,
  createMarkdownToolbar,
  mountMarkdownViewer,
  resolveInternalLinks,
} from "/static/shared/markdown/index.js";
import { createWorkspaceUserState } from "/static/shared/workspace-user-state.js";

const STATUS_LABELS = { todo: "할 일", doing: "진행 중", done: "완료" };
const dialog = document.querySelector("#task-dialog");
const form = document.querySelector("#task-form");
const titleInput = document.querySelector("#task-title");
const descriptionInput = document.querySelector("#task-description");
const taskMarkdownToolbar = document.querySelector("#task-markdown-toolbar");
const taskMarkdownPreviewElement = document.querySelector("#task-markdown-preview");
const taskDetailContent = document.querySelector("#task-detail-content");
const taskEditContent = document.querySelector("#task-edit-content");
const taskDetailTitle = document.querySelector("#task-detail-title");
const taskDetailDescription = document.querySelector("#task-description-viewer");
const taskDetailStatus = document.querySelector("#task-detail-status");
const taskDetailSchedule = document.querySelector("#task-detail-schedule");
const taskDetailTopics = document.querySelector("#task-detail-topics");
const editTaskButton = document.querySelector("#edit-task-button");
const favoriteTaskButton = document.querySelector("#favorite-task-button");
const cancelDialogButton = document.querySelector("#cancel-dialog");
const saveTaskButton = document.querySelector("#save-task-button");
const statusInput = document.querySelector("#task-status");
const taskIdInput = document.querySelector("#task-id");
const allDayInput = document.querySelector("#task-all-day");
const startAtInput = document.querySelector("#task-start-at");
const dueAtInput = document.querySelector("#task-due-at");
const startDateInput = document.querySelector("#task-start-date");
const dueDateInput = document.querySelector("#task-due-date");
const timedFields = document.querySelector("#timed-fields");
const allDayFields = document.querySelector("#all-day-fields");
const notification = document.querySelector("#notification");
const selectedList = document.querySelector("#selected-task-list");
const selectedTemplate = document.querySelector("#selected-task-template");
const deleteDialogButton = document.querySelector("#delete-task-button");
const statusFilters = [...document.querySelectorAll("[data-status-filter]")];
const overdueFilter = document.querySelector("#overdue-filter");
const createMenuButton = document.querySelector("#create-menu-button");
const createMenu = document.querySelector("#create-menu");
const topicFilterList = document.querySelector("#topic-filter-list");
const taskTopicOptions = document.querySelector("#task-topic-options");
const taskTopicEmpty = document.querySelector("#task-topic-empty");
const topicDialog = document.querySelector("#topic-dialog");
const topicForm = document.querySelector("#topic-form");
const topicIdInput = document.querySelector("#topic-id");
const topicNameInput = document.querySelector("#topic-name");
const topicColorInput = document.querySelector("#topic-color");
const topicColorValue = document.querySelector("#topic-color-value");
const deleteTopicButton = document.querySelector("#delete-topic-button");
const selectedPagination = document.querySelector("#selected-pagination");
const previousSelectedPage = document.querySelector("#previous-selected-page");
const nextSelectedPage = document.querySelector("#next-selected-page");
const selectedPageStatus = document.querySelector("#selected-page-status");
const topicFilterShell = document.querySelector("#topic-filter-shell");
const topicFilterViewport = document.querySelector("#topic-filter-viewport");
const topicOverflowButton = document.querySelector("#topic-overflow-button");
const topicOverflowPopover = document.querySelector("#topic-overflow-popover");
const topicOverflowList = document.querySelector("#topic-overflow-list");
const activeTopicCount = document.querySelector("#active-topic-count");
const calendarPage = document.querySelector(".calendar-page");
const calendarElement = document.querySelector("#calendar");
const apiBase = calendarPage.dataset.apiBase;
const csrfCookieName = calendarPage.dataset.csrfCookieName;
const notesRoute = calendarPage.dataset.notesRoute;
const notesApiBase = calendarPage.dataset.notesApiBase;
const internalLinkEndpoint = calendarPage.dataset.internalLinkEndpoint;
const userState = calendarPage.dataset.userStateBase
  ? createWorkspaceUserState({
    baseUrl: calendarPage.dataset.userStateBase,
    csrfToken: () => {
      const value = document.cookie.split("; ").find((entry) => entry.startsWith(`${csrfCookieName}=`))?.split("=").slice(1).join("=");
      return value ? decodeURIComponent(value) : "";
    },
  })
  : null;
const linkedNotesSection = document.querySelector("#linked-notes-section");
const linkedNotesList = document.querySelector("#linked-notes-list");
const linkedNotesStatus = document.querySelector("#linked-notes-status");
const attachNoteButton = document.querySelector("#attach-note-button");
const attachNoteDialog = document.querySelector("#attach-note-dialog");
const attachNoteSearch = document.querySelector("#attach-note-search");
const attachNoteResults = document.querySelector("#attach-note-results");
const attachNoteStatus = document.querySelector("#attach-note-status");
const createMeetingNote = document.querySelector("#create-meeting-note");
const createResearchNote = document.querySelector("#create-research-note");

const SELECTED_PAGE_SIZE = 5;
let tasks = [];
let topics = [];
let calendar;
let selectedKstDate = currentKstDate();
let selectedTopicId = "all";
let selectedPage = 1;
let notificationTimer;
let overdueRefreshTimer;
let taskDialogGeneration = 0;
let attachSearchGeneration = 0;
let currentTask = null;
let taskFormBaseline = "";
const markdownRenderer = createMarkdownRenderer();
const taskMarkdownViewer = mountMarkdownViewer(
  taskDetailDescription,
  "",
  {
    renderer: markdownRenderer,
    ariaLabel: "일정 설명",
    renderOptions: {
      compact: true,
      emptyMessage: "설명이 없습니다.",
    },
  },
);
const taskMarkdownPreview = attachMarkdownPreview(
  descriptionInput,
  taskMarkdownPreviewElement,
  {
    renderer: markdownRenderer,
    mode: "editor",
    availableModes: ["editor", "preview"],
    renderOptions: {
      compact: true,
      emptyMessage: "설명이 없습니다.",
    },
  },
);
const taskMarkdownEditor = attachMarkdownEditor(descriptionInput, {
  profile: "compact",
  onPreviewToggle: () => taskMarkdownPreview.toggle(),
});
const taskToolbar = createMarkdownToolbar({
  document,
  profile: "compact",
  execute: (commandId) => taskMarkdownEditor.execute(commandId),
  ariaLabel: "일정 설명 Markdown 서식",
});
taskMarkdownToolbar.append(taskToolbar);
taskMarkdownPreview.setToggleButton(
  taskToolbar.querySelector('[data-markdown-command="preview-toggle"]'),
);
attachSlashPalette(descriptionInput, taskMarkdownEditor, {
  profile: "compact",
});

function currentKstParts(date = new Date()) {
  const shifted = new Date(date.getTime() + 9 * 60 * 60 * 1000);
  const pad = (value) => String(value).padStart(2, "0");
  return {
    date: `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}`,
    hour: shifted.getUTCHours(),
    minute: shifted.getUTCMinutes(),
  };
}

function currentKstDate() {
  return currentKstParts().date;
}

function roundedDefaultRange(date = null) {
  const now = currentKstParts();
  const baseDate = date ?? now.date;
  let hour = date ? 9 : now.hour;
  let minute = date ? 0 : Math.ceil(now.minute / 30) * 30;
  if (minute === 60) {
    hour += 1;
    minute = 0;
  }
  const start = new Date(`${baseDate}T00:00:00Z`);
  start.setUTCHours(hour, minute, 0, 0);
  const end = new Date(start.getTime() + 60 * 60 * 1000);
  const localValue = (value) => {
    const pad = (part) => String(part).padStart(2, "0");
    return `${value.getUTCFullYear()}-${pad(value.getUTCMonth() + 1)}-${pad(value.getUTCDate())}T${pad(value.getUTCHours())}:${pad(value.getUTCMinutes())}`;
  };
  return { start: localValue(start), due: localValue(end) };
}

async function request(url, options = {}) {
  const token = csrfCookieName
    ? document.cookie.split("; ").find((entry) => entry.startsWith(`${csrfCookieName}=`))?.split("=").slice(1).join("=")
    : "";
  const path = url.replace(/^api\//, "");
  const response = await fetch(`${apiBase}/${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-CSRF-Token": decodeURIComponent(token) } : {}),
      ...options.headers,
    },
    ...options,
  });
  if (!response.ok) {
    let message = "요청을 처리하지 못했습니다.";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail[0]?.msg ?? message;
    } catch {
      // Retain the friendly fallback for non-JSON responses.
    }
    throw new Error(message);
  }
  if (response.status === 204) {
    await response.arrayBuffer();
    return null;
  }
  return response.json();
}

async function notesRequest(path = "", options = {}) {
  const token = csrfCookieName
    ? document.cookie.split("; ").find((entry) => entry.startsWith(`${csrfCookieName}=`))?.split("=").slice(1).join("=")
    : "";
  const response = await fetch(`${notesApiBase}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "X-CSRF-Token": decodeURIComponent(token) } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    let message = "노트 요청을 처리하지 못했습니다.";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      // Retain the stable message for non-JSON failures.
    }
    throw new Error(message);
  }
  if (response.status === 204) {
    await response.arrayBuffer();
    return null;
  }
  return response.json();
}

function showError(message) {
  window.clearTimeout(notificationTimer);
  notification.textContent = message;
  notification.hidden = false;
  notificationTimer = window.setTimeout(() => {
    notification.hidden = true;
  }, 5000);
}

function setCreateMenu(open) {
  createMenu.hidden = !open;
  createMenuButton.setAttribute("aria-expanded", String(open));
  if (open) createMenu.querySelector("[role='menuitem']")?.focus();
}

function topicStyle(element, color) {
  element.style.setProperty("--topic-color", color);
}

function appendTopicTags(container, taskTopics, limit = 2) {
  taskTopics.slice(0, limit).forEach((topic) => {
    const tag = document.createElement("span");
    tag.className = "topic-hashtag";
    tag.textContent = `#${topic.name}`;
    tag.title = `#${topic.name}`;
    topicStyle(tag, topic.color);
    container.append(tag);
  });
  if (taskTopics.length > limit) {
    const remaining = document.createElement("span");
    remaining.className = "topic-hashtag topic-hashtag-more";
    remaining.textContent = `+${taskTopics.length - limit}`;
    remaining.setAttribute(
      "aria-label",
      `추가 주제 ${taskTopics.length - limit}개`,
    );
    remaining.title = taskTopics
      .slice(limit)
      .map((topic) => `#${topic.name}`)
      .join(" ");
    container.append(remaining);
  }
}

function renderTaskTopicOptions(selectedIds = new Set()) {
  taskTopicOptions.replaceChildren();
  const orderedTopics = sortTopics(topics);
  taskTopicEmpty.hidden = orderedTopics.length > 0;
  orderedTopics.forEach((topic) => {
    const label = document.createElement("label");
    label.className = "task-topic-choice";
    topicStyle(label, topic.color);
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = topic.id;
    input.checked = selectedIds.has(topic.id);
    input.dataset.taskTopic = "";
    const text = document.createElement("span");
    text.textContent = `#${topic.name}`;
    label.append(input, text);
    taskTopicOptions.append(label);
  });
}

function openTopicDialog(topic = null) {
  topicForm.reset();
  topicIdInput.value = topic?.id ?? "";
  topicNameInput.value = topic?.name ?? "";
  topicColorInput.value = (topic?.color ?? "#375F55").toLowerCase();
  topicColorValue.value = topicColorInput.value.toUpperCase();
  document.querySelector("#topic-dialog-title").textContent = topic ? "주제 수정" : "새 주제";
  deleteTopicButton.hidden = !topic;
  topicDialog.showModal();
  topicNameInput.focus();
}

function closeTopicDialog() {
  topicDialog.close();
}

function activeFilters() {
  return {
    statuses: new Set(statusFilters.filter((input) => input.checked).map((input) => input.value)),
    overdueOnly: overdueFilter.checked,
  };
}

function visibleTasks() {
  return filterTasks(tasks, activeFilters());
}

function scheduleNextOverdueRefresh() {
  window.clearTimeout(overdueRefreshTimer);
  const now = Date.now();
  const nextDue = tasks
    .filter((task) => task.status !== "done")
    .map((task) => new Date(task.due_at).getTime())
    .filter((due) => Number.isFinite(due) && due >= now)
    .sort((left, right) => left - right)[0];
  if (nextDue === undefined) return;
  const delay = Math.min(Math.max(nextDue - now + 100, 100), 2_147_483_647);
  overdueRefreshTimer = window.setTimeout(refreshViews, delay);
}

function formatSelectedDate(date) {
  const [year, month, day] = date.split("-").map(Number);
  const weekday = new Intl.DateTimeFormat("ko-KR", {
    weekday: "short",
    timeZone: "UTC",
  }).format(new Date(Date.UTC(year, month - 1, day)));
  return `${year}년 ${month}월 ${day}일 ${weekday}`;
}

function closeTopicOverflow({ returnFocus = false } = {}) {
  topicOverflowPopover.hidden = true;
  topicOverflowButton.setAttribute("aria-expanded", "false");
  if (
    returnFocus
    && !topicOverflowButton.classList.contains("is-concealed")
  ) {
    topicOverflowButton.focus();
  }
}

function toggleTopicOverflow() {
  const opening = topicOverflowPopover.hidden;
  topicOverflowPopover.hidden = !opening;
  topicOverflowButton.setAttribute("aria-expanded", String(opening));
  if (opening) topicOverflowPopover.querySelector("[data-topic-filter]")?.focus();
}

function selectTopicFilter(id) {
  selectedTopicId = String(id);
  selectedPage = 1;
  closeTopicOverflow();
  renderSelectedDate();
}

function updateTopicOverflowButton() {
  topicOverflowButton.hidden = false;
  const overflowing = (
    topicFilterViewport.scrollWidth > topicFilterViewport.clientWidth + 1
  );
  topicOverflowButton.classList.toggle("is-concealed", !overflowing);
  topicOverflowButton.disabled = !overflowing;
  topicOverflowButton.setAttribute("aria-hidden", String(!overflowing));
  if (!overflowing) closeTopicOverflow();
}

function topicFilterItem({ id, name, color, count, topic = null }) {
  const wrapper = document.createElement("span");
  wrapper.className = "topic-filter-item";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "topic-filter-button";
  button.dataset.topicFilter = String(id);
  button.setAttribute("aria-pressed", String(String(selectedTopicId) === String(id)));
  const displayColor = color ?? (
    String(id) === "all" ? "var(--primary)" : "var(--muted)"
  );
  topicStyle(button, displayColor);

  const dot = document.createElement("span");
  dot.className = "topic-dot";
  dot.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = name;
  const countElement = document.createElement("span");
  countElement.className = "topic-filter-count";
  countElement.textContent = count;
  button.append(dot, label, countElement);
  button.addEventListener("click", () => selectTopicFilter(id));
  wrapper.append(button);

  if (topic) {
    const manage = document.createElement("button");
    manage.type = "button";
    manage.className = "topic-manage-button";
    manage.textContent = "⋯";
    manage.setAttribute("aria-label", `${topic.name} 주제 관리`);
    manage.addEventListener("click", () => {
      closeTopicOverflow();
      openTopicDialog(topic);
    });
    wrapper.append(manage);
  }
  return wrapper;
}

function renderTopicFilters(selectedDateTasks) {
  const orderedTopics = sortTopics(topics);
  const counts = topicCountsForTasks(selectedDateTasks, orderedTopics);
  const options = activeTopicOptions(orderedTopics, counts);
  const normalizedTopicId = normalizeSelectedTopicId(selectedTopicId, options);
  if (normalizedTopicId !== String(selectedTopicId)) {
    selectedTopicId = normalizedTopicId;
    selectedPage = 1;
  }
  topicFilterList.replaceChildren(...options.map(topicFilterItem));
  topicOverflowList.replaceChildren(...options.map(topicFilterItem));
  activeTopicCount.textContent = Math.max(options.length - 1, 0);
  window.requestAnimationFrame(updateTopicOverflowButton);
}

function renderSelectedDate() {
  const selectedDateTasks = tasksForDate(visibleTasks(), selectedKstDate);
  renderTopicFilters(selectedDateTasks);
  const selectedTasks = filterTasksByTopic(selectedDateTasks, selectedTopicId);
  const pageResult = paginateItems(
    selectedTasks,
    selectedPage,
    SELECTED_PAGE_SIZE,
  );
  selectedPage = pageResult.page;
  document.querySelector("#selected-date-title").textContent = formatSelectedDate(selectedKstDate);
  document.querySelector("#selected-count").textContent = pageResult.totalItems;
  selectedPagination.hidden = pageResult.totalPages <= 1;
  previousSelectedPage.disabled = pageResult.page <= 1;
  nextSelectedPage.disabled = pageResult.page >= pageResult.totalPages;
  selectedPageStatus.textContent = `${pageResult.page} / ${Math.max(pageResult.totalPages, 1)}`;
  selectedList.replaceChildren();

  if (!pageResult.totalItems) {
    const empty = document.createElement("div");
    empty.className = "selected-empty";
    empty.innerHTML = "<span aria-hidden=\"true\">○</span><p>이 날짜에는 표시할 일정이 없습니다.</p>";
    selectedList.append(empty);
    return;
  }

  pageResult.items.forEach((task) => {
    const card = selectedTemplate.content.cloneNode(true);
    const article = card.querySelector(".selected-task-card");
    article.classList.add(`status-${task.status}`);
    if (task.status === "done") article.classList.add("is-done");
    if (isTaskOverdue(task)) article.classList.add("is-overdue");
    card.querySelector(".selected-task-status").textContent = STATUS_LABELS[task.status];
    const taskTitle = card.querySelector(".selected-task-title");
    taskTitle.textContent = task.title;
    taskTitle.title = task.title;
    card.querySelector(".selected-task-time").textContent = formatTaskTime(task, selectedKstDate);
    appendTopicTags(card.querySelector(".selected-task-topics"), task.topics ?? []);
    card.querySelector(".overdue-mark").hidden = !isTaskOverdue(task);
    card.querySelector(".selected-task-main").addEventListener("click", () => openTaskDialog(task));
    selectedList.append(card);
  });
}

function refreshViews() {
  const overdueCount = tasks.filter((task) => isTaskOverdue(task)).length;
  document.querySelector("#overdue-count").textContent = overdueCount;
  document.querySelector("#filter-empty").hidden = activeFilters().statuses.size > 0;
  calendar.batchRendering(() => {
    for (const source of calendar.getEventSources()) source.remove();
    calendar.addEventSource(visibleTasks().map((task) => taskToCalendarEvent(task)));
  });
  renderSelectedDate();
  scheduleNextOverdueRefresh();
}

async function loadTasks() {
  tasks = await request("api/tasks");
  refreshViews();
}

async function loadData() {
  [tasks, topics] = await Promise.all([
    request("api/tasks"),
    request("api/topics"),
  ]);
  if (
    selectedTopicId !== "all"
    && selectedTopicId !== "unclassified"
    && !topics.some((topic) => String(topic.id) === String(selectedTopicId))
  ) {
    selectedTopicId = "unclassified";
    selectedPage = 1;
  }
  refreshViews();
}

function setAllDayMode(enabled) {
  timedFields.hidden = enabled;
  allDayFields.hidden = !enabled;
  startAtInput.required = !enabled;
  dueAtInput.required = !enabled;
  startDateInput.required = enabled;
  dueDateInput.required = enabled;
}

function taskFormSnapshot() {
  return JSON.stringify({
    title: titleInput.value,
    description: descriptionInput.value,
    status: statusInput.value,
    allDay: allDayInput.checked,
    startAt: startAtInput.value,
    dueAt: dueAtInput.value,
    startDate: startDateInput.value,
    dueDate: dueDateInput.value,
    topicIds: [...document.querySelectorAll("[data-task-topic]:checked")]
      .map((input) => Number(input.value))
      .sort((left, right) => left - right),
  });
}

function taskFormIsDirty() {
  return taskFormSnapshot() !== taskFormBaseline;
}

function formatTaskSchedule(task) {
  const start = apiTimestampToLocalInput(task.start_at);
  const due = apiTimestampToLocalInput(task.due_at);
  if (task.all_day) {
    const startDate = start.slice(0, 10);
    const dueDate = due.slice(0, 10);
    return startDate === dueDate
      ? `${startDate} · 종일`
      : `${startDate} – ${dueDate} · 종일`;
  }
  return `${start.replace("T", " ")} – ${due.replace("T", " ")}`;
}

function renderTaskDetail(task, internalLinkResolutions = new Map()) {
  taskDetailTitle.textContent = task.title;
  taskDetailStatus.textContent = STATUS_LABELS[task.status] ?? task.status;
  taskDetailSchedule.textContent = formatTaskSchedule(task);
  taskDetailTopics.textContent = task.topics?.length
    ? task.topics.map((topic) => `#${topic.name}`).join(" ")
    : "주제 없음";
  taskMarkdownViewer.update(task.description, {
    compact: true,
    emptyMessage: "설명이 없습니다.",
    internalLinkResolutions,
  });
}

function setTaskDialogMode(mode, { focus = true } = {}) {
  dialog.dataset.mode = mode;
  const detailMode = mode === "detail";
  const createMode = mode === "create";
  taskDetailContent.hidden = !detailMode;
  taskEditContent.hidden = detailMode;
  editTaskButton.hidden = !detailMode;
  saveTaskButton.hidden = detailMode;
  deleteDialogButton.hidden = createMode;
  cancelDialogButton.textContent = detailMode ? "닫기" : "취소";
  document.querySelector("#dialog-title").textContent = createMode
    ? "새 일정"
    : detailMode
      ? "일정 상세"
      : "일정 수정";
  if (!focus) return;
  if (detailMode) {
    editTaskButton.focus();
  } else {
    titleInput.focus();
  }
}

function enterTaskEditMode() {
  if (!currentTask) return;
  taskFormBaseline = taskFormSnapshot();
  taskMarkdownPreview.setMode("editor");
  setTaskDialogMode("edit");
}

function openTaskDialog(task = null, date = null) {
  const dialogGeneration = ++taskDialogGeneration;
  currentTask = task;
  favoriteTaskButton.hidden = !task || !userState;
  if (task && userState) {
    void userState.recent("calendar", task.id, {
      app_key: "calendar", label: task.title, href: calendarPage.dataset.appRoute,
    });
    void userState.favorites().then((items) => {
      if (currentTask?.id !== task.id) return;
      const active = items.some((item) => item.resource_type === "calendar" && String(item.resource_id) === String(task.id));
      favoriteTaskButton.setAttribute("aria-pressed", String(active));
      favoriteTaskButton.textContent = active ? "즐겨찾기 해제" : "즐겨찾기";
    }).catch(() => {});
  }
  form.reset();
  const defaults = roundedDefaultRange(date);
  taskIdInput.value = task?.id ?? "";
  titleInput.value = task?.title ?? "";
  descriptionInput.value = task?.description ?? "";
  taskMarkdownPreview.setRenderOptions({
    compact: true,
    emptyMessage: "설명이 없습니다.",
    internalLinkResolutions: new Map(),
  });
  taskMarkdownPreview.setMode("editor");
  statusInput.value = task?.status ?? "todo";
  allDayInput.checked = Boolean(task?.all_day);
  startAtInput.value = task ? apiTimestampToLocalInput(task.start_at) : defaults.start;
  dueAtInput.value = task ? apiTimestampToLocalInput(task.due_at) : defaults.due;
  startDateInput.value = task ? apiTimestampToLocalInput(task.start_at).slice(0, 10) : defaults.start.slice(0, 10);
  dueDateInput.value = task ? apiTimestampToLocalInput(task.due_at).slice(0, 10) : defaults.due.slice(0, 10);
  setAllDayMode(allDayInput.checked);
  renderTaskTopicOptions(
    new Set((task?.topics ?? []).map((topic) => topic.id)),
  );
  taskFormBaseline = taskFormSnapshot();
  linkedNotesSection.hidden = !task;
  linkedNotesList.replaceChildren();
  linkedNotesStatus.textContent = task ? "연결된 노트를 불러오는 중입니다." : "";
  if (task) {
    renderTaskDetail(task);
    const context = new URLSearchParams({
      source_app: "calendar",
      source_type: "event",
      source_id: String(task.id),
    });
    createMeetingNote.href = `${notesRoute}new?type=meeting_minutes&${context}`;
    createResearchNote.href = `${notesRoute}new?type=research_note&${context}`;
    loadLinkedNotes(task, dialogGeneration).catch((error) => {
      if (dialogGeneration !== taskDialogGeneration) return;
      linkedNotesStatus.textContent = error.message;
    });
    const references = collectInternalLinks(task.description);
    if (references.length) {
      resolveInternalLinks(references, {
        endpoint: internalLinkEndpoint,
      }).then((internalLinkResolutions) => {
        if (dialogGeneration !== taskDialogGeneration) return;
        taskMarkdownPreview.setRenderOptions({
          compact: true,
          emptyMessage: "설명이 없습니다.",
          internalLinkResolutions,
        });
        renderTaskDetail(task, internalLinkResolutions);
      }).catch((error) => {
        if (dialogGeneration === taskDialogGeneration) showError(error.message);
      });
    }
  }
  dialog.showModal();
  setTaskDialogMode(task ? "detail" : "create");
}

favoriteTaskButton?.addEventListener("click", async () => {
  if (!userState || !currentTask) return;
  const active = favoriteTaskButton.getAttribute("aria-pressed") === "true";
  try {
    if (active) await userState.unfavorite("calendar", currentTask.id);
    else await userState.favorite("calendar", currentTask.id, {
      app_key: "calendar", label: currentTask.title, href: calendarPage.dataset.appRoute,
    });
    favoriteTaskButton.setAttribute("aria-pressed", String(!active));
    favoriteTaskButton.textContent = active ? "즐겨찾기" : "즐겨찾기 해제";
  } catch (error) { showError(error.message); }
});

function noteTypeLabel(noteType) {
  return {
    general: "일반 메모",
    meeting_minutes: "회의록",
    research_note: "연구 노트",
  }[noteType] ?? "일반 메모";
}

async function loadLinkedNotes(task, generation = taskDialogGeneration) {
  const query = new URLSearchParams({
    source_app: "calendar",
    source_type: "event",
    source_id: String(task.id),
  });
  const notes = await notesRequest(`?${query}`);
  const linked = await Promise.all(notes.map(async (note) => {
    const links = await notesRequest(`/${note.id}/links`);
    const link = links.find((item) => (
      item.source_app === "calendar"
      && item.source_type === "event"
      && item.source_id === String(task.id)
    ));
    return { note, link };
  }));
  if (
    generation !== taskDialogGeneration
    || taskIdInput.value !== String(task.id)
  ) return;
  linkedNotesList.replaceChildren(...linked.map(({ note, link }) => {
    const row = document.createElement("div");
    row.className = "linked-note-row";
    const anchor = document.createElement("a");
    anchor.href = `${notesRoute}?note_id=${note.id}`;
    anchor.textContent = `${note.title} · ${noteTypeLabel(note.note_type)}`;
    const unlink = document.createElement("button");
    unlink.type = "button";
    unlink.className = "button button-secondary";
    unlink.textContent = "연결 해제";
    unlink.setAttribute("aria-label", `${note.title} 연결 해제`);
    unlink.addEventListener("click", async () => {
      try {
        await notesRequest(`/${note.id}/links/${link.id}`, { method: "DELETE" });
        await loadLinkedNotes(task, generation);
      } catch (error) {
        showError(error.message);
      }
    });
    row.append(anchor, unlink);
    return row;
  }));
  linkedNotesStatus.textContent = linked.length
    ? `${linked.length}개의 노트가 연결되어 있습니다.`
    : "연결된 노트가 없습니다.";
}

async function renderAttachNoteResults(task) {
  const requestGeneration = ++attachSearchGeneration;
  attachNoteStatus.textContent = "노트를 불러오는 중입니다.";
  const query = new URLSearchParams();
  if (attachNoteSearch.value.trim()) query.set("q", attachNoteSearch.value.trim());
  let notes;
  try {
    notes = await notesRequest(query.size ? `?${query}` : "");
  } catch (error) {
    if (
      requestGeneration !== attachSearchGeneration
      || taskIdInput.value !== String(task.id)
      || !attachNoteDialog.open
    ) return;
    attachNoteStatus.textContent = error.message;
    return;
  }
  if (
    requestGeneration !== attachSearchGeneration
    || taskIdInput.value !== String(task.id)
    || !attachNoteDialog.open
  ) return;
  attachNoteResults.replaceChildren(...notes.map((note) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "attach-note-result";
    button.textContent = `${note.title} · ${noteTypeLabel(note.note_type)}`;
    button.addEventListener("click", async () => {
      try {
        await notesRequest(`/${note.id}/links`, {
          method: "POST",
          body: JSON.stringify({
            source_app: "calendar",
            source_type: "event",
            source_id: String(task.id),
          }),
        });
        closeAttachNoteDialog();
        await loadLinkedNotes(task);
      } catch (error) {
        showError(error.message);
      }
    });
    return button;
  }));
  attachNoteStatus.textContent = notes.length
    ? `${notes.length}개의 노트`
    : "연결할 노트가 없습니다.";
}

function openAttachNoteDialog() {
  const task = tasks.find((item) => String(item.id) === taskIdInput.value);
  if (!task) return;
  attachNoteSearch.value = "";
  attachNoteResults.replaceChildren();
  attachNoteDialog.showModal();
  renderAttachNoteResults(task);
  attachNoteSearch.focus();
}

function closeAttachNoteDialog() {
  attachSearchGeneration += 1;
  attachNoteDialog.close();
}

function closeTaskDialog() {
  taskDialogGeneration += 1;
  attachSearchGeneration += 1;
  currentTask = null;
  dialog.close();
}

function requestCloseTaskDialog() {
  const mode = dialog.dataset.mode;
  if (mode === "edit") {
    if (
      taskFormIsDirty()
      && !window.confirm("저장하지 않은 변경사항을 취소할까요?")
    ) return;
    if (currentTask) {
      const task = currentTask;
      closeTaskDialog();
      openTaskDialog(task);
      return;
    }
  }
  if (
    mode === "create"
    && taskFormIsDirty()
    && !window.confirm("저장하지 않은 새 일정을 취소할까요?")
  ) return;
  closeTaskDialog();
}

async function saveTask(event) {
  event.preventDefault();
  const id = taskIdInput.value;
  let range;
  try {
    range = allDayInput.checked
      ? allDayInputToRange(startDateInput.value, dueDateInput.value)
      : {
          start_at: localDateTimeToKst(startAtInput.value),
          due_at: localDateTimeToKst(dueAtInput.value),
        };
    if (range.start_at > range.due_at) throw new RangeError("시작 일시는 마감 일시보다 늦을 수 없습니다.");
  } catch (error) {
    showError(error.message);
    return;
  }

  const payload = {
    title: titleInput.value,
    description: descriptionInput.value,
    status: statusInput.value,
    all_day: allDayInput.checked,
    topic_ids: [...document.querySelectorAll("[data-task-topic]:checked")]
      .map((input) => Number(input.value)),
    ...range,
  };

  try {
    const savedTask = await request(id ? `api/tasks/${id}` : "api/tasks", {
      method: id ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
    closeTaskDialog();
    await loadTasks();
    if (id) {
      const updatedTask = tasks.find(
        (task) => String(task.id) === String(savedTask.id),
      ) ?? savedTask;
      openTaskDialog(updatedTask);
    }
  } catch (error) {
    showError(error.message);
  }
}

async function saveTopic(event) {
  event.preventDefault();
  const id = topicIdInput.value;
  try {
    await request(id ? `api/topics/${id}` : "api/topics", {
      method: id ? "PATCH" : "POST",
      body: JSON.stringify({
        name: topicNameInput.value,
        color: topicColorInput.value.toUpperCase(),
      }),
    });
    closeTopicDialog();
    await loadData();
  } catch (error) {
    showError(error.message);
  }
}

async function deleteTopic() {
  const topic = topics.find((item) => String(item.id) === topicIdInput.value);
  if (!topic || !window.confirm(`“${topic.name}” 주제를 삭제할까요? 일정은 삭제되지 않습니다.`)) return;
  try {
    await request(`api/topics/${topic.id}`, { method: "DELETE" });
    selectedTopicId = "unclassified";
    selectedPage = 1;
    closeTopicDialog();
    await loadData();
  } catch (error) {
    showError(error.message);
  }
}

async function deleteTask(task) {
  if (!task || !window.confirm(`“${task.title}” 일정을 삭제할까요?`)) return;
  try {
    await request(`api/tasks/${task.id}`, { method: "DELETE" });
    closeTaskDialog();
    await loadTasks();
  } catch (error) {
    showError(error.message);
  }
}

function syncSelectedDateCell(date, preferredCell = null) {
  calendarElement.querySelectorAll("[data-date].is-selected-date").forEach((cell) => {
    cell.classList.remove("is-selected-date");
  });
  const selectedCell = preferredCell?.dataset.date === date
    ? preferredCell
    : calendarElement.querySelector(`[data-date="${date}"]`);
  selectedCell?.classList.add("is-selected-date");
}

function selectDate(date, dayElement = null) {
  selectedKstDate = date;
  selectedPage = 1;
  syncSelectedDateCell(date, dayElement);
  renderSelectedDate();
}

calendar = new Calendar(calendarElement, {
  plugins: [dayGridPlugin, classicThemePlugin],
  locales: [koLocale],
  initialView: "dayGridMonth",
  locale: "ko",
  timeZone: "Asia/Seoul",
  headerToolbar: false,
  height: "100%",
  fixedWeekCount: false,
  showNonCurrentDates: true,
  dayMaxEvents: 3,
  moreLinkContent: (info) => `+${info.num}개`,
  editable: false,
  selectable: false,
  nowIndicator: false,
  eventDisplay: "block",
  dayCellDidMount: (info) => {
    info.el.tabIndex = 0;
    info.el.setAttribute("aria-label", `날짜 선택: ${info.el.dataset.date}`);
  },
  eventClick: (info) => {
    info.jsEvent.preventDefault();
    const task = tasks.find((item) => String(item.id) === info.event.id);
    if (task) openTaskDialog(task);
  },
  eventDidMount: (info) => {
    const task = tasks.find((item) => String(item.id) === info.event.id);
    info.el.dataset.calendarEvent = "true";
    info.el.tabIndex = 0;
    info.el.setAttribute("role", "button");
    info.el.setAttribute("aria-label", task ? `일정 열기: ${task.title}` : `일정 열기: ${info.event.title}`);
    info.el.addEventListener("keydown", (event) => {
      if ((event.key === "Enter" || event.key === " ") && task) {
        event.preventDefault();
        openTaskDialog(task);
      }
    });
  },
  datesSet: (info) => {
    document.querySelector("#month-title").textContent = info.view.title;
  },
  dayCellClass: (info) => {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Seoul",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(info.date);
    const value = (type) => parts.find((part) => part.type === type)?.value;
    const date = `${value("year")}-${value("month")}-${value("day")}`;
    return date === selectedKstDate ? "is-selected-date" : "";
  },
  eventContent: (info) => {
    const wrapper = document.createElement("div");
    wrapper.className = "calendar-event-content";
    const props = info.event.extendedProps;
    const time = document.createElement("span");
    time.className = "calendar-event-time";
    time.textContent = props.allDay ? "종일" : apiTimestampToLocalInput(props.actualStartAt).slice(11);
    const title = document.createElement("span");
    title.className = "calendar-event-title";
    title.textContent = info.event.title;
    wrapper.append(time, title);
    if (props.overdue) {
      const mark = document.createElement("span");
      mark.className = "calendar-overdue-mark";
      mark.textContent = "!";
      mark.setAttribute("aria-label", "마감 초과");
      wrapper.append(mark);
    }
    return { domNodes: [wrapper] };
  },
});
calendar.render();

function dayCellFromInteractionTarget(target) {
  if (!(target instanceof Element)) return null;
  if (target.closest("[data-calendar-event], button, a[href]")) return null;
  return target.closest('[role="gridcell"][data-date]');
}

["mousedown", "touchstart"].forEach((eventName) => {
  calendarElement.addEventListener(eventName, (event) => {
    if (dayCellFromInteractionTarget(event.target)) event.stopPropagation();
  }, { capture: true });
});
calendarElement.addEventListener("click", (event) => {
  const dayCell = dayCellFromInteractionTarget(event.target);
  if (dayCell) selectDate(dayCell.dataset.date, dayCell);
});
calendarElement.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const dayCell = dayCellFromInteractionTarget(event.target);
  if (!dayCell) return;
  event.preventDefault();
  selectDate(dayCell.dataset.date, dayCell);
});

function refreshCalendarLayout(calendarInstance = calendar) {
  if (typeof calendarInstance?.updateSize === "function") calendarInstance.updateSize();
  else calendarInstance?.render();
}

window.addEventListener("workspace:layout-change", () => {
  window.requestAnimationFrame(() => {
    refreshCalendarLayout();
    updateTopicOverflowButton();
  });
});

createMenuButton.addEventListener("click", (event) => {
  event.stopPropagation();
  setCreateMenu(createMenu.hidden);
});
createMenu.addEventListener("click", (event) => event.stopPropagation());
topicFilterShell.addEventListener("click", (event) => event.stopPropagation());
topicOverflowButton.addEventListener("click", toggleTopicOverflow);
document.querySelector("#new-task-button").addEventListener("click", () => {
  setCreateMenu(false);
  openTaskDialog();
});
document.querySelector("#new-topic-button").addEventListener("click", () => {
  setCreateMenu(false);
  openTopicDialog();
});
document.addEventListener("click", () => {
  setCreateMenu(false);
  closeTopicOverflow();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !createMenu.hidden) {
    setCreateMenu(false);
    createMenuButton.focus();
  }
  if (event.key === "Escape" && !topicOverflowPopover.hidden) {
    closeTopicOverflow({ returnFocus: true });
  }
});
document.querySelector("#add-date-task").addEventListener("click", () => openTaskDialog(null, selectedKstDate));
previousSelectedPage.addEventListener("click", () => {
  selectedPage -= 1;
  renderSelectedDate();
});
nextSelectedPage.addEventListener("click", () => {
  selectedPage += 1;
  renderSelectedDate();
});
document.querySelector("#previous-month").addEventListener("click", () => calendar.prev());
document.querySelector("#next-month").addEventListener("click", () => calendar.next());
document.querySelector("#today-button").addEventListener("click", () => {
  selectDate(currentKstDate());
  calendar.today();
});
document.querySelector("#close-dialog").addEventListener("click", requestCloseTaskDialog);
document.querySelector("#cancel-dialog").addEventListener("click", requestCloseTaskDialog);
editTaskButton.addEventListener("click", enterTaskEditMode);
document.querySelector("#close-topic-dialog").addEventListener("click", closeTopicDialog);
document.querySelector("#cancel-topic-dialog").addEventListener("click", closeTopicDialog);
allDayInput.addEventListener("change", () => setAllDayMode(allDayInput.checked));
topicColorInput.addEventListener("input", () => {
  topicColorValue.value = topicColorInput.value.toUpperCase();
});
deleteDialogButton.addEventListener("click", () => {
  const task = tasks.find((item) => String(item.id) === taskIdInput.value);
  deleteTask(task);
});
form.addEventListener("submit", saveTask);
topicForm.addEventListener("submit", saveTopic);
deleteTopicButton.addEventListener("click", deleteTopic);
attachNoteButton.addEventListener("click", openAttachNoteDialog);
document.querySelector("#close-attach-note").addEventListener("click", closeAttachNoteDialog);
let attachSearchTimer = null;
attachNoteSearch.addEventListener("input", () => {
  const task = tasks.find((item) => String(item.id) === taskIdInput.value);
  window.clearTimeout(attachSearchTimer);
  attachSearchTimer = window.setTimeout(() => {
    if (task) renderAttachNoteResults(task);
  }, 180);
});
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) requestCloseTaskDialog();
});
dialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  requestCloseTaskDialog();
});
topicDialog.addEventListener("click", (event) => {
  if (event.target === topicDialog) closeTopicDialog();
});
[...statusFilters, overdueFilter].forEach((input) => input.addEventListener("change", () => {
  selectedPage = 1;
  refreshViews();
}));

new ResizeObserver(updateTopicOverflowButton).observe(topicFilterViewport);
loadData()
  .then(() => {
    const eventId = calendarDeepLinkEventId(window.location.search);
    const linkedTask = eventId
      ? tasks.find((task) => String(task.id) === eventId)
      : null;
    if (linkedTask) {
      openTaskDialog(linkedTask);
    }
  })
  .catch((error) => showError(error.message))
  .finally(() => globalThis.workspaceAppReady?.());
