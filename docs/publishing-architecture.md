# Publishing Platform Architecture

## Boundary

Notes is the authoring system. Publishing copies a saved Note into an
independent, immutable version snapshot. It never reads unsaved editor state and
does not live-reference the Note after a version is created.

```text
Saved Note
  -> PublishingService
  -> publication_versions (immutable title, summary, markdown)
  -> published viewer/search projection
```

Calendar, Notes, Publishing, and Assets use separate SQLite files. There are no
cross-database foreign keys, `ATTACH` statements, joins, or transactions.

## Snapshot and pointer invariants

`publication_versions.title`, `summary`, and `markdown` are the content
source-of-truth. The title and summary on `publications` are only a
transactionally maintained management projection.

- Every version row is immutable after insert.
- `latest_version` is the greatest version ever created and never decreases.
- `draft_version` is either `NULL` or an existing version for the publication.
- `published_version` is either `NULL` or an existing version for the
  publication.
- Publishing moves `draft_version` to `published_version` and clears the draft
  pointer. It does not rewrite the version.
- Saving a new draft copies the current saved Note into a new version.
- Note edits do not change existing versions or publication state.
- Note deletion does not delete publications or versions. The missing source is
  shown as unavailable in management context.

One Note may have multiple publications, including multiple publications of the
same type. A publication's source Note cannot be changed after creation.

## Lifecycle

```text
create -> draft -> publish -> published
                    |             |
                    +-- archive <-+
```

A published document may have an additional unpublished draft. Discarding the
draft clears its pointer but preserves immutable version history. Restoring an
old version creates a new draft version rather than mutating history.

## Storage

- Runtime database: `instance/publishing/publishing.db`
- Override: `PUBLISHING_DB_PATH=/absolute/path/publishing.db`
- Publication type manifests: `instance/publishing/types.json`
- Manifest override: `PUBLICATION_TYPES_PATH=/absolute/path/types.json`

The schema is initialized and verified during application lifespan. Startup
also rejects stored publication type IDs missing from the immutable Registry
snapshot.

## Routes

- Management API: `/api/publications*`
- Public search: `/api/publications/search`
- Publication home: `/publications/`
- Type management: `/publications/types/`
- Manual viewer: `/manual/{slug}`
- Wiki viewer: `/wiki/{slug}`
- Declarative type viewer: `/publications/view/{type_id}/{slug}`

App Registry metadata is not coupled to these routes. Publishing is a Portal
projection, not a Workspace app registration.

The public viewer's **발행물 삭제** action archives the publication. It
immediately disappears from public routes and search while the source Note and
immutable version history remain preserved. Permanent destructive deletion is
outside the MVP.

## Slugs

Slugs are explicit lowercase ASCII identifiers:

- pattern: lowercase letters, digits, and single hyphens
- maximum: 80 characters
- uniqueness: `(type_id, slug)`
- duplicates: `409 Conflict`
- reserved route words: rejected by the provider slug policy
- published slug changes and redirect aliases: out of MVP scope

## Search and events

The MVP search provider hides SQLite `LIKE` search behind
`SqliteLikePublicationSearchProvider`. It searches only the current published
version for public queries. Draft and archived documents are excluded.

Domain events are immutable service results emitted after the database
transaction succeeds. There is no event bus in the MVP. Optional synchronous
post-commit handlers may update projections; their failures never expose a
partially committed database transaction.
