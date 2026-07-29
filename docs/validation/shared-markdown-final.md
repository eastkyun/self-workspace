# Shared Markdown Core Final Validation

Recorded on 2026-07-29 for branch `feature/shared-markdown-core`.

## Automated regression

- Python non-browser: `143 passed in 13.00s`
- Node: `111 passed`
- Chromium: `33 passed in 35.11s`
- Viewports: `320`, `375`, `768`, `1024`, `1440`
- Chromium external requests: `0`
- Chromium failed requests: `0`
- Chromium console errors: `0`
- Production npm audit: `0 vulnerabilities`
- Vendored Marked/DOMPurify integrity: passed
- `git diff --check`: passed

The Python suite was run outside the managed filesystem sandbox because the
documented Starlette `TestClient` worker can block inside that sandbox.

## Database invariants

Tests used temporary pytest and Chromium databases. No test used the production
database paths.

### Calendar

```text
path:            instance/calendar/tasks.db
sha256:          ed849f9732db78da9452b131d500a5e7858854e4601c59aaa150b6782b37e397
schema sha256:   6bd76864c474cfbf7b39633db1b0bab739b8762a9504a2a6cf51d5fc423fe567
rows:            tasks=5, topics=4, task_topics=3
quick_check:     ok
user_version:    2
schema_version:  1
```

### Notes

```text
path:            instance/notes/notes.db
sha256:          5cfc94a65ecc2988fa4a6fa631469533f7f3c570003165ed95ed06c7d242a6ce
schema sha256:   77d9b3d117d00646364bb5e6697738c0be36f7334d4e55dc308ce649ccf36f32
rows:            notes=4, note_links=1
quick_check:     ok
user_version:    1
schema_version:  5
```

Both schema hashes match the pre-implementation baseline. The owner confirmed
that production row/checksum changes during implementation were their own
interactive data additions. Those rows were preserved and were not restored,
deleted, migrated, or used by automated tests.

## Contract and security evidence

- Existing Notes and Calendar API URLs and source field names are unchanged.
- Existing `notes.content` and `tasks.description` schemas are unchanged.
- Markdown source only is persisted; rendered/sanitized HTML is not stored.
- Raw HTML tokens are escaped as visible literal text before strict DOMPurify.
- Viewer DOM uses sanitized `DocumentFragment` with `replaceChildren()`.
- Script/event-handler/javascript/data/SVG/backslash-host payloads are blocked.
- Notes and Calendar share one renderer, sanitizer and command registry.
- Calendar existing events open in read-only detail mode and require an explicit
  Edit action; dirty cancel is confirmed and successful update returns to detail.
- Independent review verdict after blocker fixes: `APPROVE`.
