# Publishing Platform Baseline

Recorded on 2026-07-30 before Publishing Platform implementation.

## Git

- Branch: `feature/publishing-platform`
- Starting commit: `ee8cdd394b4fd0d1c54b9a5daa1d0ae31a232ee1`
- Starting worktree: clean
- SQLite backup:
  `instance/backups/publishing/20260730T085122+0900/`

The backup directory contains SQLite online backups and is intentionally ignored
by Git.

## Existing regression evidence

The branch starts at the already verified Shared Markdown final commit. In
accordance with the Publishing plan, the complete suites are not re-run during
the baseline phase and will run only during the final regression phase.

Evidence carried forward from the same commit:

- Python non-browser: `143 passed`
- Node: `111 passed`
- Chromium: `33 passed`
- Viewports: `320`, `375`, `768`, `1024`, `1440`
- Runtime external requests: `0`
- Failed requests: `0`
- Console errors: `0`

## App Registry invariant

```text
shared/apps.json sha256:
96125ebba5a38aef6e9bb0e33cb8027baf9dc568cd9f259b892fcc2280269e3f

deterministic registry snapshot sha256:
695c1f980cb76fd29a98a3befe1e1177d65bc61aecb8b7e6814f32871b752428
```

Registered app IDs remain exactly:

```text
calendar
notes
```

Publishing metadata must not be added to the App Registry.

## Calendar production database

```text
path:            instance/calendar/tasks.db
sha256:          f092b0b460965bc3554b53efe9c8c74275d6dbfbea15986a143db40b01f83570
schema sha256:   6bd76864c474cfbf7b39633db1b0bab739b8762a9504a2a6cf51d5fc423fe567
rows:            tasks=6, topics=4, task_topics=4
quick_check:     ok
foreign keys:    ok
user_version:    2
schema_version:  1
```

## Notes production database

```text
path:            instance/notes/notes.db
sha256:          7c99d9c49d2aa0f8a5a30edf2ba8a66f20982c099621cc1b3add5c42ac4ef233
schema sha256:   77d9b3d117d00646364bb5e6697738c0be36f7334d4e55dc308ce649ccf36f32
rows:            notes=5, note_links=2
quick_check:     ok
foreign keys:    ok
user_version:    1
schema_version:  5
```

The owner may continue to add interactive production data. Automated tests must
use injected temporary database paths. Schema hashes and test isolation are the
hard invariants; user data must not be restored, deleted, or rewritten.

## New storage baseline

```text
instance/publishing/publishing.db: absent
instance/assets/assets.db: absent
```

Tests must not create either production path. Runtime startup creation and test
fixture creation remain separate.

## Shared Markdown invariant

The deterministic aggregate SHA-256 of files below
`shared/static/markdown/` is:

```text
2bf50f417816799df346089c092a3492d5891bc9e3e71b0b384b50f23c3f959d
```

Publishing may extend the shared implementation for slug internal links and
validated local images, but it must not duplicate the renderer or sanitizer.

## Finalized contracts

- `latest_version` remains the monotonic maximum created version number.
- `published_version` and `draft_version` reference existing immutable version
  rows and obey the pointer invariants in the master plan.
- `publication_versions.title`, `summary`, and `markdown` are the content
  source-of-truth.
- `publications.title` and `summary` are transactionally maintained projection
  fields only.
- Asset references are reconciled by the server after parsing stored Markdown;
  a client-provided asset ID list is never authoritative.
