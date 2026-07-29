# Shared Markdown Core Baseline

Recorded on 2026-07-29 before Shared Markdown Core implementation.

## Git

- Branch: `feature/shared-markdown-core`
- Starting commit: `58cb8af06a1bcf298d8e73300674e399d1c4edb5`
- Starting worktree: clean
- Backup manifest:
  `instance/backups/markdown/20260729T144242+0900/manifest.json`

The backup directory is operational data and is intentionally ignored by Git.

## Regression tests

- Python non-browser: `139 passed in 12.82s`
- Node: `53 passed`
- Chromium: `30 passed in 35.36s`
- Viewports: `320`, `375`, `768`, `1024`, `1440`
- Chromium external requests: `0`
- Chromium failed requests: `0`
- Chromium console errors: `0`

Starlette `TestClient` suites run outside the managed filesystem sandbox because
the sandbox can deadlock its worker thread.

## Calendar production database

```text
path:            instance/calendar/tasks.db
sha256:          06c664757914a09c0a6d240ffc73aff66c863c1f278cf1ad0b1d6ace7ff5a178
schema sha256:   6bd76864c474cfbf7b39633db1b0bab739b8762a9504a2a6cf51d5fc423fe567
rows:            tasks=4, topics=4, task_topics=3
quick_check:     ok
foreign keys:    ok
user_version:    2
schema_version:  1
```

Observed description content:

- non-empty descriptions: `2`
- HTML-like descriptions: `0`
- Markdown-like descriptions: `0`
- maximum description length: `18`

## Notes production database

```text
path:            instance/notes/notes.db
sha256:          a2b567da0ba8174bf79fadffa43f64978715fe03ad793da47e73892673e4a84b
schema sha256:   77d9b3d117d00646364bb5e6697738c0be36f7334d4e55dc308ce649ccf36f32
rows:            notes=0, note_links=0
quick_check:     ok
foreign keys:    ok
user_version:    1
schema_version:  5
```

No production Note content existed at baseline.

## API and persistence invariants

The implementation must preserve:

- Notes CRUD: `/apps/notes/api/notes` and
  `/apps/notes/api/notes/{note_id}`
- Notes links:
  `/apps/notes/api/notes/{note_id}/links`
- Calendar CRUD: `/apps/calendar/api/tasks` and
  `/apps/calendar/api/tasks/{task_id}`
- Notes source field: `content`
- Calendar source field: `description`
- Existing request fields, response fields, validation, status codes, search,
  sorting, pin, topic, date, and logical-link behavior
- Raw source strings in SQLite; rendered or sanitized HTML must never be stored

The only planned API addition is a Portal batch internal-link resolver.

## Raw HTML security contract

Raw HTML handling is fixed before implementation:

1. Marked receives the Markdown source.
2. Marked's custom HTML-token renderer escapes only the raw token source into
   literal text such as `&lt;script&gt;`.
3. The complete rendered output passes through one strict DOMPurify allowlist.
4. Raw HTML remains visible as text but never becomes executable DOM.

The implementation must not escape the complete Marked HTML output after
rendering, remove raw HTML silently, or modify sanitized HTML through string
concatenation.

## Static rollback hashes

The backup manifest records SHA-256 values for the Notes, Calendar, and App
Shell templates, JavaScript, and CSS files that the Markdown integration may
change. Each app integration remains independently revertible.
