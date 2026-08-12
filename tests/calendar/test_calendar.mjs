import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import test from "node:test";

const adapterPath = new URL(
  "../../apps/calendar/app/static/calendar-adapter.js",
  import.meta.url,
);
const adapterSource = await readFile(adapterPath, "utf8");
const appSource = await readFile(
  new URL("../../apps/calendar/app/static/app.js", import.meta.url),
  "utf8",
);
const stylesSource = await readFile(
  new URL("../../apps/calendar/app/static/styles.css", import.meta.url),
  "utf8",
);
const indexSource = await readFile(
  new URL("../../apps/calendar/app/templates/calendar.html", import.meta.url),
  "utf8",
);
const fullCalendarBundleSource = await readFile(
  new URL(
    "../../apps/calendar/app/static/vendor/fullcalendar/7.0.2/daygrid.esm.js",
    import.meta.url,
  ),
  "utf8",
);
const shellStylesSource = await readFile(
  new URL("../../shared/static/shell.css", import.meta.url),
  "utf8",
);
const adapterUrl = `data:text/javascript;base64,${Buffer.from(adapterSource).toString("base64")}`;
const {
  KST_OFFSET,
  activeTopicOptions,
  addCalendarDays,
  allDayInputToRange,
  apiTimestampToLocalInput,
  calendarAllDayRangeToApi,
  calendarDeepLinkEventId,
  filterTasks,
  filterTasksByTopic,
  isTaskOverdue,
  localDateTimeToKst,
  normalizeSelectedTopicId,
  paginateItems,
  sortTopics,
  taskOccursOnDate,
  taskToCalendarEvent,
  tasksForDate,
  topicCountsForTasks,
} = await import(adapterUrl);

function task(overrides = {}) {
  return {
    id: 1,
    title: "일정",
    description: "설명",
    status: "todo",
    all_day: false,
    start_at: "2026-07-28T09:00:00+09:00",
    due_at: "2026-07-28T10:00:00+09:00",
    topics: [],
    ...overrides,
  };
}

test("declares KST as the fixed +09:00 offset", () => {
  assert.equal(KST_OFFSET, "+09:00");
});

test("calendar deep links accept only positive decimal event IDs", () => {
  assert.equal(calendarDeepLinkEventId("?event_id=13"), "13");
  assert.equal(calendarDeepLinkEventId("?event_id=0"), null);
  assert.equal(calendarDeepLinkEventId("?event_id=../13"), null);
});

test("uses FullCalendar v7 render and singular class APIs", () => {
  assert.match(appSource, /calendar\.render\(\)/);
  assert.match(appSource, /typeof calendarInstance\?\.updateSize === "function"/);
  assert.match(appSource, /else calendarInstance\?\.render\(\)/);
  assert.match(appSource, /workspace:layout-change/);
  assert.match(appSource, /dayCellClass:/);
  assert.doesNotMatch(appSource, /rerenderDates|dayCellClassNames|themeSystem/);
});

test("loads the local dayGrid-only FullCalendar ESM bundle", () => {
  assert.match(appSource, /from "\.\/vendor\/fullcalendar\/7\.0\.2\/daygrid\.esm\.js"/);
  assert.match(appSource, /plugins:\s*\[dayGridPlugin, classicThemePlugin\]/);
  assert.match(appSource, /locales:\s*\[koLocale\]/);
  assert.doesNotMatch(indexSource, /fullcalendar\.global|locales-all|themes\/classic\/global/);
  assert.ok(fullCalendarBundleSource.length < 300_000);
});

test("keeps the shared loader visible until initial calendar data settles", () => {
  assert.match(indexSource, /data-workspace-await-ready/);
  assert.match(appSource, /\.finally\(\(\) => globalThis\.workspaceAppReady\?\.\(\)\)/);
});

test("synchronizes the clicked date class directly on the rendered day cell", () => {
  assert.match(appSource, /function syncSelectedDateCell\(date, preferredCell = null\)/);
  assert.match(appSource, /querySelectorAll\("\[data-date\]\.is-selected-date"\)/);
  assert.match(appSource, /cell\.classList\.remove\("is-selected-date"\)/);
  assert.match(appSource, /selectedCell\?\.classList\.add\("is-selected-date"\)/);
  assert.doesNotMatch(appSource, /dateClick:/);
  assert.match(appSource, /calendarElement\.addEventListener\("click"/);
  assert.match(appSource, /dayCellFromInteractionTarget\(event\.target\)/);
  assert.match(appSource, /selectDate\(dayCell\.dataset\.date, dayCell\)/);
  assert.match(appSource, /info\.el\.dataset\.calendarEvent = "true"/);
  assert.match(appSource, /target\.closest\("\[data-calendar-event\], button, a\[href\]"\)/);
  assert.match(appSource, /target\.closest\('\[role="gridcell"\]\[data-date\]'\)/);
  assert.match(
    stylesSource,
    /\.calendar-shell \[data-date\]\.is-selected-date\s*\{[^}]*background:[^}]*var\(--primary\)[^}]*box-shadow:\s*inset 0 0 0 3px var\(--primary\)/s,
  );
});

test("avoids redundant full renders when refreshing events or selecting a date", () => {
  const refreshSource = appSource.match(
    /function refreshViews\(\) \{[\s\S]*?\n\}/,
  )?.[0];
  const selectSource = appSource.match(
    /function selectDate\(date, dayElement = null\) \{[\s\S]*?\n\}/,
  )?.[0];

  assert.ok(refreshSource);
  assert.match(refreshSource, /calendar\.batchRendering/);
  assert.match(refreshSource, /calendar\.getEventSources\(\)/);
  assert.doesNotMatch(refreshSource, /calendar\.removeAllEvents\(\)/);
  assert.doesNotMatch(refreshSource, /calendar\.render\(\)/);
  assert.ok(selectSource);
  assert.match(selectSource, /syncSelectedDateCell\(date, dayElement\)/);
  assert.doesNotMatch(selectSource, /calendar\.render\(\)/);
});

test("makes delegated day selection keyboard accessible", () => {
  assert.match(appSource, /dayCellDidMount:\s*\(info\) => \{/);
  assert.match(appSource, /info\.el\.tabIndex\s*=\s*0/);
  assert.match(appSource, /calendarElement\.addEventListener\("keydown"/);
  assert.match(appSource, /event\.key !== "Enter" && event\.key !== " "/);
  assert.match(appSource, /event\.preventDefault\(\)/);
});

test("stops date-cell pointer starts before FullCalendar hit dragging", () => {
  const guardSource = appSource.match(
    /\["mousedown", "touchstart"\]\.forEach\([\s\S]*?\n\}\);/,
  )?.[0];
  assert.ok(guardSource);
  assert.match(
    guardSource,
    /calendarElement\.addEventListener\(eventName,[\s\S]*dayCellFromInteractionTarget\(event\.target\)[\s\S]*event\.stopPropagation\(\)[\s\S]*\{ capture: true \}/,
  );
  assert.doesNotMatch(guardSource, /event\.preventDefault\(\)/);
});

test("falls back to render when the initialized calendar has no updateSize method", () => {
  const source = appSource.match(
    /function refreshCalendarLayout\(calendarInstance = calendar\) \{[\s\S]*?\n\}/,
  )?.[0];
  assert.ok(source);
  const refreshCalendarLayout = Function(
    `${source}; return refreshCalendarLayout;`,
  )();
  let renderCalls = 0;
  assert.doesNotThrow(() => refreshCalendarLayout({
    render() { renderCalls += 1; },
  }));
  assert.equal(renderCalls, 1);
  assert.match(
    appSource,
    /workspace:layout-change[\s\S]*requestAnimationFrame\(\(\) => \{[\s\S]*refreshCalendarLayout\(\)/,
  );
});

test("makes rendered calendar events keyboard accessible", () => {
  assert.match(appSource, /eventDidMount:/);
  assert.match(appSource, /info\.el\.tabIndex\s*=\s*0/);
  assert.match(appSource, /setAttribute\("role", "button"\)/);
  assert.match(appSource, /event\.key === "Enter"/);
  assert.match(appSource, /event\.key === " "/);
});

test("links Calendar details to Notes without a localhost HTTP bridge", () => {
  assert.match(indexSource, /data-notes-route=/);
  assert.match(indexSource, /id="linked-notes-section"/);
  assert.match(indexSource, /id="attach-note-dialog"/);
  assert.match(appSource, /notesRequest/);
  assert.match(appSource, /source_app:\s*"calendar"/);
  assert.match(appSource, /source_type:\s*"event"/);
  assert.doesNotMatch(appSource, /http:\/\/localhost|http:\/\/127\.0\.0\.1/);
  assert.match(indexSource, /class="task-book"/);
  assert.match(indexSource, /class="task-page task-page-primary"/);
  assert.match(indexSource, /class="task-page task-page-schedule"/);
  assert.match(stylesSource, /\.linked-notes-actions \.button\s*\{[^}]*align-items:\s*center[^}]*justify-content:\s*center/s);
  assert.match(appSource, /taskDialogGeneration/);
  assert.match(appSource, /attachSearchGeneration/);
});

test("calendar description uses the shared compact Markdown editor", () => {
  assert.match(indexSource, /static\/shared\/markdown\/markdown\.css/);
  assert.match(indexSource, /id="task-markdown-toolbar"/);
  assert.match(indexSource, /id="task-markdown-preview"/);
  assert.match(indexSource, /id="task-description-viewer"/);
  assert.match(indexSource, /id="edit-task-button"/);
  assert.match(appSource, /profile: "compact"/);
  assert.match(appSource, /mountMarkdownViewer\(/);
  assert.match(appSource, /setTaskDialogMode\(task \? "detail" : "create"\)/);
  assert.match(appSource, /function enterTaskEditMode\(\)/);
  assert.match(appSource, /function taskFormIsDirty\(\)/);
  assert.match(appSource, /collectInternalLinks\(task\.description\)/);
});

test("keeps the calendar inside the viewport without page scrolling", () => {
  assert.match(appSource, /height:\s*"100%"/);
  assert.match(shellStylesSource, /height:\s*100dvh/);
  assert.match(shellStylesSource, /body\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(stylesSource, /\.calendar-page\s*\{[^}]*height:\s*100%[^}]*min-height:\s*0[^}]*overflow:\s*hidden/s);
  assert.match(stylesSource, /\.calendar-content\s*\{[^}]*min-height:\s*0[^}]*overflow:\s*hidden/s);
  assert.match(stylesSource, /\.fc-daygrid-day-frame\s*\{\s*min-height:\s*0;/);
});

test("configures selected-date pagination in 5-item pages", () => {
  assert.match(appSource, /SELECTED_PAGE_SIZE\s*=\s*5/);
  assert.match(appSource, /paginateItems\(/);
  assert.match(appSource, /selectedPage\s*=\s*1/);
});

test("uses readable two-line cards and summarizes excess topic tags", () => {
  assert.match(appSource, /appendTopicTags\(container, taskTopics, limit = 2\)/);
  assert.match(appSource, /topic-hashtag-more/);
  assert.match(shellStylesSource, /PretendardVariable\.woff2/);
  assert.match(stylesSource, /-webkit-line-clamp:\s*2/);
});

test("centers the header create button in a fixed square", () => {
  assert.match(
    stylesSource,
    /\.create-menu-trigger\s*\{[^}]*position:\s*relative[^}]*width:\s*44px[^}]*height:\s*44px/s,
  );
  assert.match(indexSource, /class="create-plus-icon"[^>]*viewBox="0 0 20 20"/);
  assert.match(indexSource, /d="M10 4V16M4 10H16"/);
  assert.match(
    stylesSource,
    /\.create-plus-icon\s*\{[^}]*position:\s*absolute[^}]*top:\s*50%[^}]*left:\s*50%[^}]*transform:\s*translate\(-50%, -50%\)/s,
  );
});

test("configures an accessible active-topic overflow popover", () => {
  assert.match(indexSource, /id="topic-overflow-button"/);
  assert.match(indexSource, /aria-expanded="false"/);
  assert.match(indexSource, /id="topic-overflow-popover"/);
  assert.match(indexSource, /class="control-chevron-icon topic-overflow-icon"/);
  assert.doesNotMatch(appSource, /topicOverflowButton\.textContent/);
  assert.match(appSource, /new ResizeObserver\(updateTopicOverflowButton\)/);
  assert.match(appSource, /scrollWidth\s*>\s*topicFilterViewport\.clientWidth/);
  assert.match(appSource, /event\.key === "Escape"/);
  assert.match(
    indexSource,
    /id="previous-selected-page"[\s\S]*M12\.5 5L7\.5 10L12\.5 15/,
  );
  assert.match(
    indexSource,
    /id="next-selected-page"[\s\S]*M7\.5 5L12\.5 10L7\.5 15/,
  );
  assert.match(
    stylesSource,
    /\.topic-overflow-button\s*\{[^}]*display:\s*block[^}]*place-self:\s*center[^}]*width:\s*30px[^}]*height:\s*30px/s,
  );
  assert.match(stylesSource, /\.topic-filter-nav::\-webkit-scrollbar\s*\{\s*display:\s*none;/);
});

test("adds the KST offset and seconds to a datetime-local value", () => {
  assert.equal(
    localDateTimeToKst("2026-07-28T09:30"),
    "2026-07-28T09:30:00+09:00",
  );
});

test("returns an empty timestamp for an empty datetime-local value", () => {
  assert.equal(localDateTimeToKst(""), "");
});

test("converts a UTC API timestamp to the same instant in a KST form value", () => {
  assert.equal(
    apiTimestampToLocalInput("2026-07-28T00:30:45Z"),
    "2026-07-28T09:30",
  );
});

test("converts a non-KST offset API timestamp to the same instant in KST", () => {
  assert.equal(
    apiTimestampToLocalInput("2026-07-27T20:30:00-04:00"),
    "2026-07-28T09:30",
  );
});

test("adds calendar days across a month boundary", () => {
  assert.equal(addCalendarDays("2026-07-31", 1), "2026-08-01");
});

test("normalizes one all-day input date to inclusive KST day boundaries", () => {
  assert.deepEqual(allDayInputToRange("2026-07-28", "2026-07-28"), {
    start_at: "2026-07-28T00:00:00+09:00",
    due_at: "2026-07-28T23:59:59+09:00",
  });
});

test("restores FullCalendar's exclusive all-day end to the inclusive API end", () => {
  assert.deepEqual(
    calendarAllDayRangeToApi("2026-07-28", "2026-08-01"),
    {
      start_at: "2026-07-28T00:00:00+09:00",
      due_at: "2026-07-31T23:59:59+09:00",
    },
  );
});

test("maps a same-day timed task without changing its actual end", () => {
  const event = taskToCalendarEvent(task(), new Date("2026-07-28T00:00:00Z"));

  assert.equal(event.start, "2026-07-28T09:00:00+09:00");
  assert.equal(event.end, "2026-07-28T10:00:00+09:00");
  assert.equal(event.allDay, false);
});

test("maps a midnight-crossing timed task without adding a calendar day", () => {
  const value = task({
    start_at: "2026-07-28T23:30:00+09:00",
    due_at: "2026-07-29T00:30:00+09:00",
  });

  assert.equal(taskToCalendarEvent(value).end, value.due_at);
});

test("maps a multi-day timed task without changing its actual end", () => {
  const value = task({
    start_at: "2026-07-28T12:00:00+09:00",
    due_at: "2026-07-31T18:00:00+09:00",
  });

  assert.equal(taskToCalendarEvent(value).end, value.due_at);
});

test("maps a month-crossing timed task without changing its actual end", () => {
  const value = task({
    start_at: "2026-07-31T23:00:00+09:00",
    due_at: "2026-08-01T01:00:00+09:00",
  });

  assert.equal(taskToCalendarEvent(value).end, value.due_at);
});

test("omits FullCalendar end for an equal-start-and-due timed task", () => {
  const value = task({ due_at: "2026-07-28T09:00:00+09:00" });
  const event = taskToCalendarEvent(value);

  assert.equal(Object.hasOwn(event, "end"), false);
});

test("preserves an equal-start-and-due task's actual due timestamp", () => {
  const value = task({ due_at: "2026-07-28T09:00:00+09:00" });

  assert.equal(
    taskToCalendarEvent(value).extendedProps.actualDueAt,
    value.due_at,
  );
});

test("uses the next date as FullCalendar's exclusive end for a one-day all-day task", () => {
  const event = taskToCalendarEvent(
    task({
      all_day: true,
      start_at: "2026-07-28T00:00:00+09:00",
      due_at: "2026-07-28T23:59:59+09:00",
    }),
  );

  assert.equal(event.start, "2026-07-28");
  assert.equal(event.end, "2026-07-29");
  assert.equal(event.allDay, true);
});

test("keeps the final inclusive date visible for a multi-day all-day task", () => {
  const event = taskToCalendarEvent(
    task({
      all_day: true,
      start_at: "2026-07-28T00:00:00+09:00",
      due_at: "2026-07-31T23:59:59+09:00",
    }),
  );

  assert.equal(event.end, "2026-08-01");
});

test("preserves API fields needed to edit a calendar event", () => {
  const value = task();
  const event = taskToCalendarEvent(value);

  assert.equal(event.id, String(value.id));
  assert.equal(event.title, value.title);
  assert.equal(event.extendedProps.actualDueAt, value.due_at);
  assert.equal(event.extendedProps.description, value.description);
  assert.equal(event.extendedProps.status, value.status);
});

test("uses FullCalendar v7 singular className for status and overdue styling", () => {
  const event = taskToCalendarEvent(
    task({
      all_day: true,
      due_at: "2026-07-28T23:59:59+09:00",
    }),
    new Date("2026-07-29T00:00:00Z"),
  );

  assert.equal(event.className, "status-todo is-all-day is-overdue");
  assert.equal(Object.hasOwn(event, "classNames"), false);
});

test("includes both date endpoints when checking task occurrence", () => {
  const value = task({
    start_at: "2026-07-28T23:30:00+09:00",
    due_at: "2026-07-30T00:30:00+09:00",
  });

  assert.equal(taskOccursOnDate(value, "2026-07-28"), true);
  assert.equal(taskOccursOnDate(value, "2026-07-30"), true);
});

test("excludes dates outside a task's inclusive KST date range", () => {
  const value = task({
    start_at: "2026-07-28T23:30:00+09:00",
    due_at: "2026-07-30T00:30:00+09:00",
  });

  assert.equal(taskOccursOnDate(value, "2026-07-27"), false);
  assert.equal(taskOccursOnDate(value, "2026-07-31"), false);
});

test("sorts tasks for a selected date by start and due timestamp", () => {
  const later = task({
    id: 2,
    start_at: "2026-07-28T12:00:00+09:00",
    due_at: "2026-07-28T13:00:00+09:00",
  });
  const earlier = task({ id: 1 });

  assert.deepEqual(
    tasksForDate([later, earlier], "2026-07-28").map(({ id }) => id),
    [1, 2],
  );
});

test("filters tasks to the selected statuses", () => {
  const tasks = [task({ id: 1 }), task({ id: 2, status: "done" })];

  assert.deepEqual(
    filterTasks(
      tasks,
      { statuses: new Set(["done"]), overdueOnly: false },
      new Date("2026-07-28T00:00:00Z"),
    ).map(({ id }) => id),
    [2],
  );
});

test("sorts topics case-insensitively by name and then ID", () => {
  const topics = [
    { id: 3, name: "beta" },
    { id: 2, name: "Alpha" },
    { id: 1, name: "alpha" },
  ];

  assert.deepEqual(
    sortTopics(topics).map(({ id }) => id),
    [1, 2, 3],
  );
  assert.deepEqual(topics.map(({ id }) => id), [3, 2, 1]);
});

test("filters the selected-date list by all, unclassified, or a topic", () => {
  const work = { id: 1, name: "업무", color: "#3366CC" };
  const personal = { id: 2, name: "개인", color: "#CC6633" };
  const tasks = [
    task({ id: 1, topics: [work, personal] }),
    task({ id: 2, topics: [personal] }),
    task({ id: 3 }),
  ];

  assert.deepEqual(
    filterTasksByTopic(tasks, "all").map(({ id }) => id),
    [1, 2, 3],
  );
  assert.deepEqual(
    filterTasksByTopic(tasks, work.id).map(({ id }) => id),
    [1],
  );
  assert.deepEqual(
    filterTasksByTopic(tasks, personal.id).map(({ id }) => id),
    [1, 2],
  );
  assert.deepEqual(
    filterTasksByTopic(tasks, "unclassified").map(({ id }) => id),
    [3],
  );
});

test("counts a multi-topic task once in all and once in each related topic", () => {
  const topics = [
    { id: 1, name: "업무", color: "#3366CC" },
    { id: 2, name: "회사", color: "#3366CC" },
  ];
  const counts = topicCountsForTasks(
    [
      task({ id: 1, topics }),
      task({ id: 2, topics: [topics[0]] }),
      task({ id: 3 }),
    ],
    topics,
  );

  assert.deepEqual(counts, {
    all: 3,
    unclassified: 1,
    byTopic: { 1: 2, 2: 1 },
  });
});

test("shows only active topics after all and conditional unclassified", () => {
  const topics = [
    { id: 3, name: "beta", color: "#333333" },
    { id: 2, name: "Alpha", color: "#222222" },
    { id: 1, name: "alpha", color: "#111111" },
  ];
  const counts = {
    all: 4,
    unclassified: 1,
    byTopic: { 1: 2, 2: 0, 3: 1 },
  };

  assert.deepEqual(
    activeTopicOptions(topics, counts).map(({ id, count }) => [id, count]),
    [["all", 4], ["unclassified", 1], ["1", 2], ["3", 1]],
  );
  assert.deepEqual(topics.map(({ id }) => id), [3, 2, 1]);
});

test("hides zero-count unclassified and falls back from inactive topics", () => {
  const options = activeTopicOptions(
    [{ id: 1, name: "업무", color: "#3366CC" }],
    { all: 0, unclassified: 0, byTopic: { 1: 0 } },
  );

  assert.deepEqual(options.map(({ id }) => id), ["all"]);
  assert.equal(normalizeSelectedTopicId("unclassified", options), "all");
  assert.equal(normalizeSelectedTopicId(1, options), "all");
  assert.equal(normalizeSelectedTopicId("all", options), "all");
});

test("keeps an active selected topic normalized as a string", () => {
  const options = activeTopicOptions(
    [{ id: 7, name: "업무", color: "#3366CC" }],
    { all: 2, unclassified: 0, byTopic: { 7: 2 } },
  );

  assert.equal(normalizeSelectedTopicId(7, options), "7");
});

test("paginates selected-date items at 5-item boundaries", () => {
  const items = Array.from({ length: 11 }, (_, index) => index + 1);

  assert.deepEqual(paginateItems([], 1), {
    items: [],
    page: 1,
    pageSize: 5,
    totalItems: 0,
    totalPages: 0,
  });
  assert.deepEqual(paginateItems(items.slice(0, 5), 1).items, items.slice(0, 5));
  assert.deepEqual(paginateItems(items.slice(0, 6), 2).items, [6]);
  assert.equal(paginateItems(items.slice(0, 10), 1).totalPages, 2);
  assert.equal(paginateItems(items, 1).totalPages, 3);
  assert.deepEqual(paginateItems(items, 3).items, [11]);
});

test("clamps invalid selected-date pages without mutating the source", () => {
  const items = Array.from({ length: 6 }, (_, index) => ({ id: index + 1 }));
  const snapshot = structuredClone(items);

  assert.equal(paginateItems(items, 0).page, 1);
  assert.equal(paginateItems(items, -4).page, 1);
  assert.equal(paginateItems(items, 99).page, 2);
  assert.equal(paginateItems(items, "invalid").page, 1);
  assert.deepEqual(items, snapshot);
  assert.throws(() => paginateItems(items, 1, 0), RangeError);
});

test("marks an unfinished task overdue only when its due instant is earlier than now", () => {
  assert.equal(
    isTaskOverdue(task(), new Date("2026-07-28T01:00:01Z")),
    true,
  );
});

test("does not mark a task overdue when its due instant equals now", () => {
  assert.equal(
    isTaskOverdue(task(), new Date("2026-07-28T01:00:00Z")),
    false,
  );
});

test("does not mark a completed task overdue", () => {
  assert.equal(
    isTaskOverdue(
      task({ status: "done" }),
      new Date("2026-07-28T01:00:01Z"),
    ),
    false,
  );
});

test("overdue-only filtering excludes completed and equality-boundary tasks", () => {
  const tasks = [
    task({ id: 1, due_at: "2026-07-28T09:59:59+09:00" }),
    task({ id: 2, due_at: "2026-07-28T10:00:00+09:00" }),
    task({
      id: 3,
      status: "done",
      due_at: "2026-07-28T09:59:59+09:00",
    }),
  ];

  assert.deepEqual(
    filterTasks(
      tasks,
      { statuses: new Set(["todo", "done"]), overdueOnly: true },
      new Date("2026-07-28T01:00:00Z"),
    ).map(({ id }) => id),
    [1],
  );
});

test("KST conversions are identical in UTC, New York, and Seoul host timezones", () => {
  const probe = `
    import { readFile } from "node:fs/promises";
    const source = await readFile(${JSON.stringify(adapterPath.pathname)}, "utf8");
    const url = "data:text/javascript;base64," + Buffer.from(source).toString("base64");
    const adapter = await import(url);
    const value = {
      local: adapter.localDateTimeToKst("2026-07-28T09:30"),
      api: adapter.apiTimestampToLocalInput("2026-07-28T00:30:00Z"),
      overdue: adapter.isTaskOverdue(
        { status: "todo", due_at: "2026-07-28T10:00:00+09:00" },
        new Date("2026-07-28T01:00:00Z")
      ),
    };
    process.stdout.write(JSON.stringify(value));
  `;
  const results = ["UTC", "America/New_York", "Asia/Seoul"].map((TZ) => {
    const result = spawnSync(
      process.execPath,
      ["--input-type=module", "--eval", probe],
      {
        encoding: "utf8",
        env: { ...process.env, TZ },
      },
    );
    assert.equal(result.status, 0, result.stderr);
    return result.stdout;
  });

  assert.equal(new Set(results).size, 1);
});
