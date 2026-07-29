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

## Release baseline

Recorded on 2026-07-29 after the Notes integration and UI review were
completed.

- Release commit before this documentation update:
  `6bda7440a73100097bf6887008e4032c4d6b7d4a`
- Python non-browser: `130 passed`
- Node: `52 passed`
- Chromium: `25 passed`
- Chromium request health: no external requests, failed requests, or console
  errors
- Verified viewports: `320`, `375`, `768`, `1024`, `1440`

The original Calendar checksum above remains the immutable implementation
baseline. During final interactive review, the user intentionally changed
Calendar records through a separately running development server. Those
changes occurred outside the pytest and Chromium processes; the E2E server
continued to use injected databases under `/tmp/pytest-of-eastvirus/`.

The resulting operational data baseline is:

```text
Calendar
sha256:        06c664757914a09c0a6d240ffc73aff66c863c1f278cf1ad0b1d6ace7ff5a178
rows:          tasks=4, topics=4, task_topics=3
maximum IDs:   tasks=14, topics=4
schema hash:   6bd76864c474cfbf7b39633db1b0bab739b8762a9504a2a6cf51d5fc423fe567
quick_check:   ok
foreign keys:  ok
user_version:  2
schema_version: 1

Notes
sha256:        a2b567da0ba8174bf79fadffa43f64978715fe03ad793da47e73892673e4a84b
rows:          notes=0, note_links=0
schema hash:   77d9b3d117d00646364bb5e6697738c0be36f7334d4e55dc308ce649ccf36f32
quick_check:   ok
foreign keys:  ok
user_version:  1
schema_version: 5
```

This release baseline supersedes the earlier operational row counts for
future before/after checks; it does not rewrite or obscure the original
pre-implementation evidence.
