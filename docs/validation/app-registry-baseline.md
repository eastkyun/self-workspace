# App Registry System Baseline

Recorded on 2026-07-28 before Registry implementation.

## Git

- Branch: `feature/app-registry-system`
- Starting commit: `7e3631b`
- Worktree before recording: clean

## Tests

- Python non-browser regression: `64 passed in 7.19s`
- Node regression: `2 passed`
- Chromium regression: `11 passed in 14.66s`
- Runtime assertions in the Chromium suite: no external network requests,
  failed requests, or console errors

The managed filesystem sandbox can deadlock Starlette `TestClient`. The same
targeted Portal tests passed outside that sandbox (`10 passed in 0.57s`), so
all TestClient-based regression commands are run in the approved execution
environment.

## Production Database

- Path: `instance/calendar/tasks.db`
- SHA-256 before tests:
  `376e033fe3679d50410067ad9507f1519bf26597097f5211649a07ca6f7f4e54`
- SHA-256 after tests:
  `376e033fe3679d50410067ad9507f1519bf26597097f5211649a07ca6f7f4e54`
- `PRAGMA quick_check`: `ok`
- `PRAGMA user_version`: `2`
- `PRAGMA schema_version`: `1`

An earlier App Shell completion record contained a different database
checksum. The current checksum was present before this branch changed any
workspace file and is the immutable baseline for this Registry task.
