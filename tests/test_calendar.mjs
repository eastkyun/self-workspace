import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import test from "node:test";

const adapterPath = new URL("../app/static/calendar-adapter.js", import.meta.url);
const adapterSource = await readFile(adapterPath, "utf8");
const appSource = await readFile(
  new URL("../app/static/app.js", import.meta.url),
  "utf8",
);
const stylesSource = await readFile(
  new URL("../app/static/styles.css", import.meta.url),
  "utf8",
);
const adapterUrl = `data:text/javascript;base64,${Buffer.from(adapterSource).toString("base64")}`;
const {
  KST_OFFSET,
  addCalendarDays,
  allDayInputToRange,
  apiTimestampToLocalInput,
  calendarAllDayRangeToApi,
  filterTasks,
  isTaskOverdue,
  localDateTimeToKst,
  taskOccursOnDate,
  taskToCalendarEvent,
  tasksForDate,
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
    ...overrides,
  };
}

test("declares KST as the fixed +09:00 offset", () => {
  assert.equal(KST_OFFSET, "+09:00");
});

test("uses FullCalendar v7 render and singular class APIs", () => {
  assert.match(appSource, /calendar\.render\(\)/);
  assert.match(appSource, /dayCellClass:/);
  assert.doesNotMatch(appSource, /rerenderDates|dayCellClassNames|themeSystem/);
});

test("keeps the calendar inside the viewport without page scrolling", () => {
  assert.match(appSource, /height:\s*"100%"/);
  assert.match(stylesSource, /height:\s*100dvh/);
  assert.match(stylesSource, /body\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(stylesSource, /\.app-content\s*\{[^}]*min-height:\s*0[^}]*overflow:\s*hidden/s);
  assert.match(stylesSource, /\.fc-daygrid-day-frame\s*\{\s*min-height:\s*0;/);
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
