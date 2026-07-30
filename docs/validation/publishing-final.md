# Publishing Platform Final Validation

Recorded on 2026-07-30 from `feature/publishing-platform`.

## Regression

- Python non-browser: `236 passed`
- Node test files: `18 passed`
- Chromium: `42 unique tests passed`
- Viewports: `320`, `375`, `768`, `1024`, `1440`
- Instrumented external requests: `0`
- Instrumented failed requests: `0`
- Instrumented console errors: `0`
- `git diff --check`: passed

Chromium tests were executed in seven disjoint groups because the command
runner has a shorter wall-time ceiling than the complete browser suite. The
groups cover all 42 collected node IDs, including Calendar, Notes, Publishing,
clipboard images, Publication Type hot-refresh, archive deletion, and all five
viewport matrices.

## Storage isolation

Pytest uses injected temporary Calendar, Notes, Publishing, Asset, and
Publication Type manifest paths. Chromium sets:

- `CALENDAR_DB_PATH`
- `NOTES_DB_PATH`
- `PUBLISHING_DB_PATH`
- `ASSET_DB_PATH`
- `PUBLICATION_TYPES_PATH`

The live server asserts that its temporary Calendar, Notes, and Publishing
databases are empty before browser scenarios begin.

## Production invariants

### App Registry

`shared/apps.json` remained byte-identical:

```text
96125ebba5a38aef6e9bb0e33cb8027baf9dc568cd9f259b892fcc2280269e3f
```

### Notes

The immediate pre/post validation snapshot was identical:

```text
sha256:        a644c65f2339b5dc2fdc75d1a6e017c8a966ad17be563181dcffb04ebcf4fa67
schema sha256: 77d9b3d117d00646364bb5e6697738c0be36f7334d4e55dc308ce649ccf36f32
rows:          notes=1, note_links=0
quick_check:   ok
```

### Calendar concurrent user activity

Calendar schema, row counts, `user_version`, `quick_check`, and foreign-key
check remained stable:

```text
schema sha256: 6bd76864c474cfbf7b39633db1b0bab739b8762a9504a2a6cf51d5fc423fe567
rows:          tasks=6, topics=4, task_topics=4
user_version:  2
quick_check:   ok
```

The raw file checksum changed from
`f092b0b460965bc3554b53efe9c8c74275d6dbfbea15986a143db40b01f83570`
to
`2fa3211fb6f034bf2f3e37a0fb2152d4b16186a4d271583c0bd8acc5e64dcc28`
during final validation. At the same time, production Publishing and Asset
rows increased and Calendar task 16 contained a newer user edit. This is
recorded as concurrent interactive user activity, not normalized or rolled
back. Automated test paths remained temporary and no production data was
restored, deleted, or rewritten by the validation workflow.

## UX acceptance

- Korean clipboard image filename uploads without non-Latin header failure.
- Publications home provides library summary, clear CTAs, document cards, and
  an accessible empty state.
- Long publications scroll inside the App Shell main region.
- Publication Markdown uses a distinct reading surface.
- Publication Type CRUD applies immediately through a validated atomic
  registry snapshot swap; Workspace restart is not required.
- Publication deletion removes the public document by archiving it while
  preserving the source Note and immutable version history.
