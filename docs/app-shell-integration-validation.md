# App Shell Integration Validation

## Baseline

- Date: 2026-07-28
- Branch: `feature/calendar-app-shell`
- Baseline commit: `e567548`
- Python (Calendar, Portal, integration): `58 passed in 7.42s`
- Node: `2 passed`
- Chromium E2E: `3 passed in 6.52s`
- Runtime database: `instance/calendar/tasks.db`
- Database SHA-256: `9732ca860b5daa9608c43b6178c0838a36525bfe3cecd8083ccd804b5acc3cb6`
- SQLite `PRAGMA user_version`: `2`
- SQLite `PRAGMA quick_check`: `ok`

The Calendar App Shell work must stop if the API/static URL contracts, schema
version, database checksum, same-origin network policy, or console/request health
regress.

## Final verification

- Python including Chromium E2E: `75 passed in 20.67s`
- Node suites: `2 passed`
- Dedicated Chromium E2E: `11 passed in 11.17s`
- Viewports:
  - `320 × 800`: passed
  - `375 × 667`: passed
  - `768 × 1024`: passed
  - `1024 × 768`: passed
  - `1440 × 900`: passed
- Every viewport asserted:
  - no horizontal document overflow
  - body height equals viewport height
  - Calendar and date panel remain within the main content width
  - no external network request
  - no failed request
  - no console error
- Runtime database SHA-256 after all tests:
  `9732ca860b5daa9608c43b6178c0838a36525bfe3cecd8083ccd804b5acc3cb6`
- SQLite `PRAGMA user_version`: `2`
- SQLite `PRAGMA quick_check`: `ok`
- SQLite foreign key errors: `0`
- Runtime task count: `2`

## E2E database isolation incident

An early keyboard-accessibility E2E run exposed that `portal.main.create_app()`
passed the default database path explicitly, preventing `CALENDAR_DB_PATH` from
selecting the test database. The stop condition detected the checksum change.

Recovery and prevention:

1. The runtime DB was atomically restored from the verified promoted backup.
2. The restored checksum exactly matched the baseline checksum.
3. Portal DB resolution now delegates optional paths to Calendar's established
   explicit path → `CALENDAR_DB_PATH` → `TODO_DB_PATH` → default precedence.
4. The E2E server now refuses to start unless its temporary database is empty.
5. The full test and viewport suites completed without changing the runtime DB.
