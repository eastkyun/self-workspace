import {
  allDayInputToRange,
  apiTimestampToLocalInput,
  filterTasks,
  filterTasksByTopic,
  formatTaskTime,
  isTaskOverdue,
  localDateTimeToKst,
  sortTopics,
  taskToCalendarEvent,
  tasksForDate,
  topicCountsForTasks,
} from "./calendar-adapter.js";

const STATUS_LABELS = { todo: "할 일", doing: "진행 중", done: "완료" };
const dialog = document.querySelector("#task-dialog");
const form = document.querySelector("#task-form");
const titleInput = document.querySelector("#task-title");
const descriptionInput = document.querySelector("#task-description");
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

let tasks = [];
let topics = [];
let calendar;
let selectedKstDate = currentKstDate();
let selectedTopicId = "all";
let notificationTimer;
let overdueRefreshTimer;

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
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...options.headers },
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
  return response.status === 204 ? null : response.json();
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

function appendTopicTags(container, taskTopics) {
  taskTopics.forEach((topic) => {
    const tag = document.createElement("span");
    tag.className = "topic-hashtag";
    tag.textContent = `#${topic.name}`;
    topicStyle(tag, topic.color);
    container.append(tag);
  });
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

function topicFilterItem({ id, name, color, count, topic = null }) {
  const wrapper = document.createElement("span");
  wrapper.className = "topic-filter-item";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "topic-filter-button";
  button.dataset.topicFilter = String(id);
  button.setAttribute("aria-pressed", String(String(selectedTopicId) === String(id)));
  topicStyle(button, color);

  const dot = document.createElement("span");
  dot.className = "topic-dot";
  dot.setAttribute("aria-hidden", "true");
  const label = document.createElement("span");
  label.textContent = name;
  const countElement = document.createElement("span");
  countElement.className = "topic-filter-count";
  countElement.textContent = count;
  button.append(dot, label, countElement);
  button.addEventListener("click", () => {
    selectedTopicId = String(id);
    renderSelectedDate();
  });
  wrapper.append(button);

  if (topic) {
    const manage = document.createElement("button");
    manage.type = "button";
    manage.className = "topic-manage-button";
    manage.textContent = "⋯";
    manage.setAttribute("aria-label", `${topic.name} 주제 관리`);
    manage.addEventListener("click", () => openTopicDialog(topic));
    wrapper.append(manage);
  }
  return wrapper;
}

function renderTopicFilters(selectedDateTasks) {
  const orderedTopics = sortTopics(topics);
  const counts = topicCountsForTasks(selectedDateTasks, orderedTopics);
  topicFilterList.replaceChildren(
    topicFilterItem({
      id: "all",
      name: "전체",
      color: "var(--primary)",
      count: counts.all,
    }),
    topicFilterItem({
      id: "unclassified",
      name: "미분류",
      color: "var(--muted)",
      count: counts.unclassified,
    }),
    ...orderedTopics.map((topic) =>
      topicFilterItem({
        id: topic.id,
        name: topic.name,
        color: topic.color,
        count: counts.byTopic[String(topic.id)] ?? 0,
        topic,
      })
    ),
  );
}

function renderSelectedDate() {
  const selectedDateTasks = tasksForDate(visibleTasks(), selectedKstDate);
  renderTopicFilters(selectedDateTasks);
  const selectedTasks = filterTasksByTopic(selectedDateTasks, selectedTopicId);
  document.querySelector("#selected-date-title").textContent = formatSelectedDate(selectedKstDate);
  document.querySelector("#selected-count").textContent = selectedTasks.length;
  selectedList.replaceChildren();

  if (!selectedTasks.length) {
    const empty = document.createElement("div");
    empty.className = "selected-empty";
    empty.innerHTML = "<span aria-hidden=\"true\">○</span><p>이 날짜에는 표시할 일정이 없습니다.</p>";
    selectedList.append(empty);
    return;
  }

  selectedTasks.forEach((task) => {
    const card = selectedTemplate.content.cloneNode(true);
    const article = card.querySelector(".selected-task-card");
    article.classList.add(`status-${task.status}`);
    if (task.status === "done") article.classList.add("is-done");
    if (isTaskOverdue(task)) article.classList.add("is-overdue");
    card.querySelector(".selected-task-status").textContent = STATUS_LABELS[task.status];
    card.querySelector(".selected-task-title").textContent = task.title;
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
  calendar.removeAllEvents();
  calendar.addEventSource(visibleTasks().map((task) => taskToCalendarEvent(task)));
  calendar.render();
  renderSelectedDate();
  scheduleNextOverdueRefresh();
}

async function loadTasks() {
  tasks = await request("/api/tasks");
  refreshViews();
}

async function loadData() {
  [tasks, topics] = await Promise.all([
    request("/api/tasks"),
    request("/api/topics"),
  ]);
  if (
    selectedTopicId !== "all"
    && selectedTopicId !== "unclassified"
    && !topics.some((topic) => String(topic.id) === String(selectedTopicId))
  ) {
    selectedTopicId = "unclassified";
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

function openTaskDialog(task = null, date = null) {
  form.reset();
  const defaults = roundedDefaultRange(date);
  taskIdInput.value = task?.id ?? "";
  titleInput.value = task?.title ?? "";
  descriptionInput.value = task?.description ?? "";
  statusInput.value = task?.status ?? "todo";
  allDayInput.checked = Boolean(task?.all_day);
  startAtInput.value = task ? apiTimestampToLocalInput(task.start_at) : defaults.start;
  dueAtInput.value = task ? apiTimestampToLocalInput(task.due_at) : defaults.due;
  startDateInput.value = task ? apiTimestampToLocalInput(task.start_at).slice(0, 10) : defaults.start.slice(0, 10);
  dueDateInput.value = task ? apiTimestampToLocalInput(task.due_at).slice(0, 10) : defaults.due.slice(0, 10);
  document.querySelector("#dialog-title").textContent = task ? "일정 수정" : "새 일정";
  deleteDialogButton.hidden = !task;
  setAllDayMode(allDayInput.checked);
  renderTaskTopicOptions(
    new Set((task?.topics ?? []).map((topic) => topic.id)),
  );
  dialog.showModal();
  titleInput.focus();
}

function closeTaskDialog() {
  dialog.close();
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
    await request(id ? `/api/tasks/${id}` : "/api/tasks", {
      method: id ? "PATCH" : "POST",
      body: JSON.stringify(payload),
    });
    closeTaskDialog();
    await loadTasks();
  } catch (error) {
    showError(error.message);
  }
}

async function saveTopic(event) {
  event.preventDefault();
  const id = topicIdInput.value;
  try {
    await request(id ? `/api/topics/${id}` : "/api/topics", {
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
    await request(`/api/topics/${topic.id}`, { method: "DELETE" });
    selectedTopicId = "unclassified";
    closeTopicDialog();
    await loadData();
  } catch (error) {
    showError(error.message);
  }
}

async function deleteTask(task) {
  if (!task || !window.confirm(`“${task.title}” 일정을 삭제할까요?`)) return;
  try {
    await request(`/api/tasks/${task.id}`, { method: "DELETE" });
    closeTaskDialog();
    await loadTasks();
  } catch (error) {
    showError(error.message);
  }
}

function selectDate(date) {
  selectedKstDate = date;
  calendar.render();
  renderSelectedDate();
}

calendar = new FullCalendar.Calendar(document.querySelector("#calendar"), {
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
  dateClick: (info) => selectDate(info.dateStr),
  eventClick: (info) => {
    info.jsEvent.preventDefault();
    const task = tasks.find((item) => String(item.id) === info.event.id);
    if (task) openTaskDialog(task);
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

createMenuButton.addEventListener("click", (event) => {
  event.stopPropagation();
  setCreateMenu(createMenu.hidden);
});
createMenu.addEventListener("click", (event) => event.stopPropagation());
document.querySelector("#new-task-button").addEventListener("click", () => {
  setCreateMenu(false);
  openTaskDialog();
});
document.querySelector("#new-topic-button").addEventListener("click", () => {
  setCreateMenu(false);
  openTopicDialog();
});
document.addEventListener("click", () => setCreateMenu(false));
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !createMenu.hidden) {
    setCreateMenu(false);
    createMenuButton.focus();
  }
});
document.querySelector("#add-date-task").addEventListener("click", () => openTaskDialog(null, selectedKstDate));
document.querySelector("#previous-month").addEventListener("click", () => calendar.prev());
document.querySelector("#next-month").addEventListener("click", () => calendar.next());
document.querySelector("#today-button").addEventListener("click", () => {
  selectDate(currentKstDate());
  calendar.today();
});
document.querySelector("#close-dialog").addEventListener("click", closeTaskDialog);
document.querySelector("#cancel-dialog").addEventListener("click", closeTaskDialog);
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
dialog.addEventListener("click", (event) => {
  if (event.target === dialog) closeTaskDialog();
});
topicDialog.addEventListener("click", (event) => {
  if (event.target === topicDialog) closeTopicDialog();
});
[...statusFilters, overdueFilter].forEach((input) => input.addEventListener("change", refreshViews));

loadData().catch((error) => showError(error.message));
