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
