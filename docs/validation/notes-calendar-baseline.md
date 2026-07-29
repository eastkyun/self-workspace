# Notes and Calendar Integration Baseline

Recorded on 2026-07-29 before Notes implementation.

## Git

- Branch: `feature/notes-calendar-linking`
- Starting commit: `2f7047e`
- Worktree: clean

## Regression tests

- Python non-browser: `96 passed in 9.45s`
- Node: `2 suites passed`
- Chromium: `11 passed in 14.27s`
- Chromium request health: no external requests, failed requests, or console errors
- Viewports: 320, 375, 768, 1024, and 1440 pixels

Starlette `TestClient` regression commands run in the approved non-sandboxed
test environment because the managed filesystem sandbox can deadlock its
worker thread.

## Calendar production database

- Path: `instance/calendar/tasks.db`
- SHA-256 before tests:
  `376e033fe3679d50410067ad9507f1519bf26597097f5211649a07ca6f7f4e54`
- SHA-256 after tests:
  `376e033fe3679d50410067ad9507f1519bf26597097f5211649a07ca6f7f4e54`
- Schema SHA-256:
  `6bd76864c474cfbf7b39633db1b0bab739b8762a9504a2a6cf51d5fc423fe567`
- Rows: `tasks=3`, `topics=3`, `task_topics=2`
- Maximum IDs: `tasks=13`, `topics=3`
- `PRAGMA quick_check`: `ok`
- `PRAGMA foreign_key_check`: no rows
- `PRAGMA user_version`: `2`
- `PRAGMA schema_version`: `1`
- File size: `40960`
- mtime_ns: `1785229799535305975`

Calendar checksum, row counts, and schema are stop conditions for this task.

## Notes production database

- Expected path: `instance/notes/notes.db`
- State before implementation: absent

Tests must inject temporary Notes and Calendar database paths. They must not
create the production Notes database or mutate the production Calendar
database.

## Final validation

- Python non-browser: `125 passed`
- Node: Calendar, Portal, Notes `3 suites passed`
- Chromium: `20 passed`
- Viewports: `320`, `375`, `768`, `1024`, `1440`
- External runtime requests: `0`
- Browser failed requests: `0`
- Browser console errors: `0`

Calendar production database remained unchanged:

```text
sha256:      376e033fe3679d50410067ad9507f1519bf26597097f5211649a07ca6f7f4e54
rows:        tasks=3, topics=3, task_topics=2
schema hash: 6bd76864c474cfbf7b39633db1b0bab739b8762a9504a2a6cf51d5fc423fe567
quick_check: ok
user_version: 2
```

Notes production DB는 구현 이후 정상 app startup 또는 사용자 실행으로 생성될 수 있으므로 Calendar
불변 checksum 대상에서 제외했습니다. 최종 회귀의 pytest와 Chromium server는 각각 임시
Calendar/Notes DB 경로를 명시적으로 주입했습니다. 최종 확인 시 운영 Notes DB quick check는
`ok`, `user_version=1`이었으며 기존 사용자 row를 수정하거나 삭제하지 않았습니다.
