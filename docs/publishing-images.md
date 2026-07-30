# Workspace Images and Publishing

## Shared asset boundary

Clipboard images use the Workspace Asset Service rather than Notes or
Publishing-specific blob tables.

- metadata DB: `instance/assets/assets.db`
- blob storage: `instance/assets/blobs/`
- override DB: `ASSET_DB_PATH=/absolute/path/assets.db`
- blob root defaults beside the selected Asset DB

Assets are content-addressed by SHA-256 and deduplicated. PNG, JPEG, and static
WebP are accepted after server-side image decoding and normalization checks.
GIF, SVG, raw HTML, remote images, and executable formats are rejected.

## Markdown contract

The editor inserts:

```markdown
![설명](image://123)
```

The database stores only this Markdown source. Shared Markdown rendering
collects the asset ID, resolves it through `/api/assets/resolve`, and emits an
`img` element only for a validated same-origin `/api/assets/{id}/content` URL.
The strict sanitizer rejects remote, `data:`, `javascript:`, SVG, and malformed
image sources.

Clipboard filenames are URI-encoded in the request header and decoded by the
server. This supports Korean and other Unicode filenames without violating the
browser Fetch header byte restrictions.

## Reference reconcile

The client never submits an authoritative asset ID list. After Note save or
Publication version creation, the server reads the stored Markdown through an
official source reader, parses `image://` tokens, and reconciles
`asset_references`.

Owners currently supported:

- `note` / `content`
- `publication_version` / `markdown`

Deleting or editing an owner removes its references, not the asset immediately.
Orphan garbage collection is an explicit future maintenance operation; it is
not performed during a cross-app save.

## Security and lifecycle

- upload size is bounded while streaming
- media type and decoded bytes must agree
- storage keys are generated, never derived from the original filename
- original filename is metadata only
- content responses set `nosniff`
- no external CDN or runtime request
- no cross-database foreign key, join, or transaction
- publication versions retain references independently of source Notes
