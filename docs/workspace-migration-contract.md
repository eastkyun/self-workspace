# Workspace coordinated migration contract

> This contract governs the legacy Calendar v2, Notes v1, Publishing v1 and Assets v1 transition to the
> workspace-aware release. It is a coordinated cutover across physically separate SQLite databases, not a
> cross-database transaction.

## 1. Fixed inventory and target ownership

Migration inventory is all of the following as one release set:

| Component | Source | Target responsibility |
|---|---|---|
| Identity | none | New Identity DB v1 with explicit bootstrap user, PERSONAL workspace, Owner membership, roles, permissions, app state and audit |
| Calendar | `instance/calendar/tasks.db`, schema v2 | Workspace/creator/updater/deletion columns; workspace-scoped topic uniqueness; preserve `due_at` |
| Notes | `instance/notes/notes.db`, schema v1 | Workspace/creator/updater/deletion columns on notes; workspace on links |
| Publishing | `instance/publishing/publishing.db`, schema v1 | Workspace/creator/visibility/public ID, tenant slug uniqueness, public aliases and public asset projection |
| Publication types | `instance/publishing/types.json` | Byte/content-preserved manifest or an explicitly versioned compatible transform |
| Assets | `instance/assets/assets.db`, schema v1 | Workspace/uploader/public ID, tenant SHA dedup, workspace references |
| Asset blobs | `instance/assets/blobs/` | Byte-preserved blob tree with path/hash/size inventory |
| App catalog source | `shared/apps.json` | Immutable catalog checksum synchronized into Identity; workspace enablement stored separately |

Every existing domain row is assigned to exactly one explicitly created bootstrap user's PERSONAL
workspace. The operator must never infer ownership from note text, filenames, Git author, OS user, current
session, email-like content or prior application activity.

## 2. Bootstrap command and secret handling

The approved interface is:

```text
python -m tools.workspace_cli bootstrap-owner --email <email>
```

The CLI normalizes and displays the email for confirmation but reads the password from an interactive
non-echo prompt. It must not accept the password as a command argument, environment value printed by the
tool, stdin pipe used by ordinary operation, or manifest field. Output may contain generated non-secret
user/workspace/release UUIDs, never password material, session/reset/invitation tokens or their hashes.

Bootstrap is one Identity DB transaction: user, PERSONAL workspace, active Owner membership, built-in
roles and permission bindings, default workspace apps and audit record either all commit or all roll back.
Repeat execution is idempotent only when every immutable identity and workspace field matches; otherwise it
stops with no mutation. The resulting `bootstrap_user_id`, `bootstrap_workspace_id` and membership ID are
recorded in the migration manifest.

## 3. Release manifest

`migration-manifest.json` is created before staging migration and updated through atomic temp-file write,
flush, `fsync`, rename and parent-directory `fsync`. Its schema is versioned. Unknown schema versions,
missing required keys, duplicate paths, mismatched release IDs or invalid state transitions stop both CLI
and application startup.

Minimum structure (hash strings are lowercase SHA-256 hex):

```json
{
  "manifest_version": 1,
  "release_id": "uuid",
  "created_at": "RFC3339 UTC",
  "source": {
    "git_commit": "40-hex commit",
    "git_branch": "feature/workspace-membership-rbac",
    "worktree_porcelain_sha256": "sha256",
    "application_version": "string",
    "single_worker_asserted": true
  },
  "bootstrap": {
    "email_normalized_sha256": "sha256",
    "user_id": "uuid",
    "workspace_id": "uuid",
    "membership_id": "uuid"
  },
  "catalog": {
    "path": "shared/apps.json",
    "sha256_before": "sha256",
    "manifest_checksum_after": "sha256"
  },
  "artifacts": {
    "identity": {
      "source_path": null,
      "staging_path": "release-relative path",
      "target_path": "instance/identity/identity.db",
      "from_version": 0,
      "to_version": 1,
      "release_id": "uuid",
      "state": "verified",
      "before": null,
      "after": {"bytes": 0, "file_sha256": "sha256", "schema_sha256": "sha256", "content_sha256": "sha256", "rows": {}, "quick_check": "ok", "foreign_key_orphans": 0}
    },
    "calendar": {},
    "notes": {},
    "publishing": {},
    "assets": {},
    "publication_types": {},
    "asset_blobs": {}
  },
  "audits": {
    "actor_orphans": 0,
    "workspace_orphans": 0,
    "publication_note_orphans": 0,
    "asset_reference_orphans": 0,
    "allowed_snapshot_orphans": []
  },
  "compatibility": {
    "canonical_smoke": "passed",
    "legacy_api_smoke": "passed",
    "public_alias_smoke": "passed"
  },
  "cutover": {
    "maintenance_lock": true,
    "write_fence": true,
    "bundle_verified": true,
    "state": "ready",
    "steps": []
  }
}
```

Each SQLite `before`/`after` record contains bytes, file/schema/content SHA-256, `user_version`, per-table
row counts, `quick_check`, and foreign-key orphan count. File manifests contain bytes and SHA-256. Blob
manifests contain a sorted relative path, byte size and SHA-256 per file plus an aggregate manifest hash.
The normalized email is not stored in plaintext in this migration evidence; operational Identity data
contains it under the restricted data policy.

Artifact state transitions are strictly:

```text
pending → applied → verified → promoted
```

Failure may transition the release to `rollback_required` or `rolled_back`; it cannot skip verification,
move backward to appear successful, or reuse a release ID with different bytes.

## 4. Staging, verification and promote sequence

1. Assert one host/one Uvicorn worker; acquire maintenance lock and establish where the later write fence
   will be activated.
2. Reject an unexplained dirty worktree. Record branch, commit, application version and source artifact
   absolute paths without embedding secrets.
3. Back up all four databases, Identity target if present, publication types and asset blobs into one
   release-specific immutable backup set. Capture and fsync its manifest.
4. Run `quick_check`, foreign-key check, schema hash, logical content hash, row count and file checksum on
   the sources. Any failure stops before staging.
5. Create bootstrap Identity DB and explicit Owner/PERSONAL workspace. Record only non-secret identifiers.
6. Copy each domain into a release-specific staging directory. Never migrate an operational source path.
7. Apply versioned migrations in fixed order: **Calendar → Notes → Publishing → Assets**. Backfill every
   row with the same bootstrap workspace/user IDs.
8. Audit actor/workspace references, Calendar/Note links, publication source Note IDs, asset references,
   publication aliases and public immutable asset projection.
9. Require source and staging user-visible row/content/blob preservation. Allowed source-snapshot orphans
   must be enumerated with resource type/ID, count and reason; no other orphan is accepted.
10. Start a temporary application whose DB/blob/type paths all point to staging. Run canonical `/api/v1`,
    legacy API and public alias smoke tests plus workspace isolation and public asset tests.
11. Freeze compatible application binary, staging artifacts and manifest under one `release_id`; verify
    the bundle again without writes.
12. Activate the write fence. Recapture operational hashes and compare with the backed-up `before` values.
    Drift means new writes occurred and the release returns to staging; do not overwrite them.
13. Mark cutover `ready`, fsync, replace artifacts in the recorded deterministic order, and fsync each
    target directory after every replace. Record every step before advancing.
14. Application startup reads release IDs/versions for Identity and all four domains. It refuses a mixed,
    incomplete or unverifiable set.
15. Start read-only, execute health/canonical/legacy/public smoke, then enable writes. Mark every artifact
    `promoted` and cutover `complete` only after verification and fsync.

No step uses SQLite `ATTACH`, cross-database joins, cross-database foreign keys or a pretend global
transaction. Domain migration functions receive explicit context and own only their staging connection.

## 5. Preservation rules

- Calendar `due_at` retains its name and API meaning; no opportunistic `end_at` rename.
- Notes/Calendar user-visible content and timestamps remain byte/semantic equivalent except added tenancy,
  actor and soft-delete metadata.
- Existing publications are assigned to the bootstrap workspace and backfilled `PUBLIC`; immutable version
  content remains preserved.
- `/manual/{slug}`, `/wiki/{slug}` and `/publications/view/{type_id}/{slug}` become explicit route aliases,
  not global tenant-local slug lookups.
- Existing blobs remain byte-identical. Asset dedup changes from global SHA to `(workspace_id, sha256)`
  without deleting the sole legacy copy.
- Every public asset must be in the immutable version projection of an active, published PUBLIC
  publication in the same workspace.
- App Registry bytes/checksum are evidence; workspace-specific app choices are new Identity rows and do
  not mutate the global registry.

## 6. Stop and rollback gates

Stop before promote on any of the following:

- missing explicit bootstrap user/workspace, password exposure, invalid UUID, or partial bootstrap;
- production/operational path selected as a staging target;
- failed backup, `quick_check`, foreign-key check, schema/version validation or `fsync`;
- row, visible content, version, publication alias, type manifest or blob loss/change without an approved
  deterministic transform;
- an unexpected actor/workspace/source/asset orphan or an unreasoned snapshot orphan;
- failed canonical, legacy, public, isolation, CSRF or permission smoke;
- source drift after write fence, multiple workers/instances, unknown manifest version/state, release ID
  mismatch, or dirty/unexplained worktree;
- any runtime artifact not bound to the exact compatible binary release.

If replacement is interrupted or post-replace verification fails, keep writes disabled, mark
`rollback_required`, stop the application and restore **Identity plus all four domain databases, type
manifest and blobs from the same backup set**. Partial rollback is prohibited. Verify restored hashes,
schema versions, `quick_check`, foreign keys and legacy read smoke before removing maintenance mode. Git
revert does not restore runtime data and is never accepted as the database rollback procedure.

## 7. Evidence required to close migration

The release evidence directory must contain the fsynced manifest and state journal, source/staging/promoted
hashes and counts, backup inventory, cross-DB audit report, full Python/Node/Chromium results, canonical and
legacy API smoke, public alias/asset checks, workspace isolation matrix, secret/log scan, worker/process
assertion, network-host trace and rollback rehearsal result. Evidence contains identifiers and hashes only;
content bodies, email plaintext, passwords and tokens are excluded.

Migration is complete only when all artifacts are `promoted`, startup accepted one common release ID,
read-only and write smoke passed, the write fence was intentionally removed, and no stop gate remains.
