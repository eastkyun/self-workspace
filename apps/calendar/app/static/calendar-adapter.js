export const KST_OFFSET = "+09:00";

const API_TIMESTAMP = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2}))?(?:Z|[+-]\d{2}:\d{2})$/;
const LOCAL_DATETIME = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::(\d{2}))?$/;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

function pad(value) {
  return String(value).padStart(2, "0");
}

function instantToKstParts(value) {
  const instant = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(instant.getTime())) {
    throw new TypeError("유효한 timezone-aware 일시가 필요합니다.");
  }
  const shifted = new Date(instant.getTime() + 9 * 60 * 60 * 1000);
  const date = `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())}`;
  const time = `${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}:${pad(shifted.getUTCSeconds())}`;
  return { date, time };
}

export function localDateTimeToKst(value) {
  if (!value) return "";
  const match = LOCAL_DATETIME.exec(value);
  if (!match) throw new TypeError("날짜와 시간을 입력해 주세요.");
  return `${match[1]}T${match[2]}:${match[3] ?? "00"}${KST_OFFSET}`;
}

export function apiTimestampToLocalInput(value) {
  if (!value || !API_TIMESTAMP.test(value)) return "";
  const { date, time } = instantToKstParts(value);
  return `${date}T${time.slice(0, 5)}`;
}

export function addCalendarDays(date, days) {
  if (!DATE_ONLY.test(date)) throw new TypeError("YYYY-MM-DD 날짜가 필요합니다.");
  const [year, month, day] = date.split("-").map(Number);
  const result = new Date(Date.UTC(year, month - 1, day + days));
  return `${result.getUTCFullYear()}-${pad(result.getUTCMonth() + 1)}-${pad(result.getUTCDate())}`;
}

export function allDayInputToRange(startDate, endDate) {
  if (!DATE_ONLY.test(startDate) || !DATE_ONLY.test(endDate) || startDate > endDate) {
    throw new RangeError("종일 일정의 시작일은 마감일보다 늦을 수 없습니다.");
  }
  return {
    start_at: `${startDate}T00:00:00${KST_OFFSET}`,
    due_at: `${endDate}T23:59:59${KST_OFFSET}`,
  };
}

export function calendarAllDayRangeToApi(startDate, exclusiveEndDate) {
  return allDayInputToRange(startDate, addCalendarDays(exclusiveEndDate, -1));
}

export function isTaskOverdue(task, now = new Date()) {
  if (task.status === "done") return false;
  const nowMs = now instanceof Date ? now.getTime() : new Date(now).getTime();
  const dueMs = new Date(task.due_at).getTime();
  return Number.isFinite(dueMs) && Number.isFinite(nowMs) && dueMs < nowMs;
}

export function taskOccursOnDate(task, date) {
  if (!DATE_ONLY.test(date)) return false;
  const startDate = apiTimestampToLocalInput(task.start_at).slice(0, 10);
  const dueDate = apiTimestampToLocalInput(task.due_at).slice(0, 10);
  return Boolean(startDate && dueDate && startDate <= date && date <= dueDate);
}

export function tasksForDate(tasks, date) {
  return tasks
    .filter((task) => taskOccursOnDate(task, date))
    .sort((left, right) =>
      left.start_at.localeCompare(right.start_at)
      || left.due_at.localeCompare(right.due_at)
      || left.id - right.id
    );
}

export function filterTasks(tasks, filters = {}, now = new Date()) {
  const rawStatuses = filters.statuses ?? ["todo", "doing", "done"];
  const statuses = rawStatuses instanceof Set ? rawStatuses : new Set(rawStatuses);
  return tasks.filter((task) =>
    statuses.has(task.status) && (!filters.overdueOnly || isTaskOverdue(task, now))
  );
}

function topicSortKey(name) {
  return name.replace(/[A-Z]/g, (letter) => letter.toLowerCase());
}

export function sortTopics(topics) {
  return [...topics].sort((left, right) => {
    const leftName = topicSortKey(left.name);
    const rightName = topicSortKey(right.name);
    if (leftName < rightName) return -1;
    if (leftName > rightName) return 1;
    return left.id - right.id;
  });
}

export function filterTasksByTopic(tasks, selectedTopicId) {
  if (selectedTopicId === "all") return [...tasks];
  if (selectedTopicId === "unclassified") {
    return tasks.filter((task) => !task.topics?.length);
  }
  return tasks.filter((task) =>
    task.topics?.some((topic) => String(topic.id) === String(selectedTopicId))
  );
}

export function topicCountsForTasks(tasks, topics) {
  const byTopic = Object.fromEntries(topics.map((topic) => [String(topic.id), 0]));
  let unclassified = 0;
  tasks.forEach((task) => {
    if (!task.topics?.length) {
      unclassified += 1;
      return;
    }
    new Set(task.topics.map((topic) => String(topic.id))).forEach((topicId) => {
      if (Object.hasOwn(byTopic, topicId)) byTopic[topicId] += 1;
    });
  });
  return { all: tasks.length, unclassified, byTopic };
}

export function activeTopicOptions(topics, counts) {
  const options = [
    {
      id: "all",
      name: "전체",
      color: null,
      count: counts.all,
      topic: null,
    },
  ];
  if (counts.unclassified > 0) {
    options.push({
      id: "unclassified",
      name: "미분류",
      color: null,
      count: counts.unclassified,
      topic: null,
    });
  }
  sortTopics(topics).forEach((topic) => {
    const count = counts.byTopic[String(topic.id)] ?? 0;
    if (count > 0) {
      options.push({
        id: String(topic.id),
        name: topic.name,
        color: topic.color,
        count,
        topic,
      });
    }
  });
  return options;
}

export function normalizeSelectedTopicId(selectedTopicId, options) {
  const normalized = String(selectedTopicId);
  return options.some((option) => String(option.id) === normalized)
    ? normalized
    : "all";
}

export function paginateItems(items, requestedPage, pageSize = 5) {
  if (!Number.isInteger(pageSize) || pageSize <= 0) {
    throw new RangeError("pageSize must be a positive integer");
  }
  const totalItems = items.length;
  const totalPages = Math.ceil(totalItems / pageSize);
  const numericPage = Number.isFinite(Number(requestedPage))
    ? Math.trunc(Number(requestedPage))
    : 1;
  const page = totalPages
    ? Math.min(Math.max(numericPage, 1), totalPages)
    : 1;
  const start = (page - 1) * pageSize;
  return {
    items: items.slice(start, start + pageSize),
    page,
    pageSize,
    totalItems,
    totalPages,
  };
}

export function taskToCalendarEvent(task, now = new Date()) {
  const overdue = isTaskOverdue(task, now);
  const classNames = [`status-${task.status}`];
  if (task.all_day) classNames.push("is-all-day");
  if (overdue) classNames.push("is-overdue");

  const event = {
    id: String(task.id),
    title: task.title,
    start: task.all_day ? apiTimestampToLocalInput(task.start_at).slice(0, 10) : task.start_at,
    allDay: Boolean(task.all_day),
    className: classNames.join(" "),
    extendedProps: {
      taskId: task.id,
      description: task.description,
      status: task.status,
      allDay: Boolean(task.all_day),
      actualStartAt: task.start_at,
      actualDueAt: task.due_at,
      overdue,
    },
  };

  if (task.all_day) {
    const lastDate = apiTimestampToLocalInput(task.due_at).slice(0, 10);
    event.end = addCalendarDays(lastDate, 1);
  } else if (task.start_at !== task.due_at) {
    event.end = task.due_at;
  }
  return event;
}

export function formatTaskTime(task, selectedDate = "") {
  if (task.all_day) return "종일";
  const start = apiTimestampToLocalInput(task.start_at);
  const due = apiTimestampToLocalInput(task.due_at);
  const startDate = start.slice(0, 10);
  const dueDate = due.slice(0, 10);
  if (startDate === dueDate) return `${start.slice(11)}–${due.slice(11)}`;
  if (selectedDate === startDate) return `${start.slice(11)} 시작`;
  if (selectedDate === dueDate) return `${due.slice(11)} 마감`;
  return `${startDate} ${start.slice(11)} – ${dueDate} ${due.slice(11)}`;
}
