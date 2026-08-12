# Workspace identity and tenancy baseline

> Capture date: 2026-08-03 (Asia/Seoul)  
> Scope: Phase 0 evidence for `.omx/plans/workspace-membership-rbac-dashboard.md`  
> This is a pre-migration record, not an authorization design or a migration approval.

## 1. Source baseline

| Item | Captured value |
|---|---|
| Git branch | `feature/publishing-platform` |
| Git commit | `39c3e924a438dcae83d7ce90ccd0a0d4db8ee8ca` |
| Application shape | FastAPI modular monolith, Jinja templates, vanilla JavaScript |
| Composition root | `portal/main.py` |
| Physical data boundary | Four independent SQLite files: Calendar, Notes, Publishing, Assets |
| Runtime assumption for the MVP | One host, one Uvicorn worker, local persistent `instance/` storage |

The current product name “Workspace” describes the application shell, not a tenant. There is no user,
session, workspace membership, workspace-scoped app state, or permission engine in this baseline.
`portal/main.py` composes the four domains and the global immutable registry. This structure and the four
domain database boundaries are retained. Phase 0 does **not** introduce `frontend/` and `backend/`
directories, Docker, an ORM, or a database merger.

The capture worktree was not assumed clean merely from the commit ID. Every future migration manifest
must record `git status --porcelain` separately and must stop on unexplained changes. Files created by
concurrent implementation lanes are not part of this historical Git identity unless committed into the
release identified by the manifest.

## 2. API baseline

All current application and API routes are anonymous. There is no `/api/v1` surface and no request-level
workspace context. Routes with an integer identifier perform domain-local lookup; they are therefore
explicit migration inventory, not examples to copy into workspace-aware repositories.

| Surface | Current routes |
|---|---|
| Portal | `GET /`, `GET /api/hub/apps`, `POST /api/internal-links/resolve` |
| Calendar (mounted at `/apps/calendar`) | `GET /`; `GET,POST /api/topics`; `PATCH,DELETE /api/topics/{topic_id}`; `GET,POST /api/tasks`; `PATCH,DELETE /api/tasks/{task_id}` |
| Notes (mounted at `/apps/notes`) | `GET /`, `GET /new`; `GET,POST /api/notes`; `GET,PUT,DELETE /api/notes/{note_id}`; `GET,POST /api/notes/{note_id}/links`; `DELETE /api/notes/{note_id}/links/{link_id}` |
| Publishing | `GET /api/publication-types`; `GET,POST /api/publication-type-manifests`; `PUT,DELETE /api/publication-type-manifests/{type_id}`; `POST /api/publications/preview-from-note`; `GET,POST /api/publications`; `GET /api/publications/search`; `GET /api/publications/{publication_id}`; draft, publish, archive, restore and version routes under `/api/publications/{publication_id}`; HTML/public aliases below |
| Assets | `POST /api/assets/images`, `POST /api/assets/resolve`, `POST /api/assets/references/reconcile`, `GET /api/assets/{asset_id}/content` |
| Existing publication URLs | `GET /publications/`, `GET /publications/types/`, `GET /manual/{slug}`, `GET /wiki/{slug}`, `GET /publications/view/{type_id}/{slug}` |
| Static mounts | `/static/shared`, `/static/publishing`, `/static`, plus Calendar and Notes sub-app static mounts |

Compatibility contract:

- New authenticated endpoints use `/api/v1`.
- Existing endpoints are not silently removed or redirected. During the compatibility period they must
  call the same authentication, workspace and authorization services as canonical endpoints.
- Existing public publication aliases remain anonymous only for migrated `PUBLIC` publications. A
  soft-deleted workspace makes them return `404`.
- A non-member or cross-workspace resource identifier returns `404`; an active member lacking an
  operation permission returns `403`.
- Success response bodies are not globally enveloped during Phase 0. New error bodies use `code`,
  `message`, `request_id`, and optional `details`.

## 3. Database and file baseline

The following values were read through SQLite URI `mode=ro`. `content_sha256` is a SHA-256 of canonical
JSON containing table names and every row ordered by `rowid`; it does not disclose content. `schema_sha256`
is a SHA-256 of ordered non-internal `sqlite_master` definitions. A release migration must create a fresh
manifest immediately before its own backup; these values are the Phase 0 reference and must not be reused
after legitimate writes.

| Domain | Path | Bytes | `user_version` | Rows | File SHA-256 | Schema SHA-256 | Content SHA-256 |
|---|---|---:|---:|---|---|---|---|
| Calendar | `instance/calendar/tasks.db` | 40,960 | 2 | `tasks=6`, `topics=4`, `task_topics=4` | `6afa26c2e77049dbffb826aea18d2737e913b30abee91367a699e8c8b0537943` | `8f772a0393c65224d0d3b5ca693f89424cfd6b282a7ac1f597eccd824da0eb6f` | `90e4f8052233e2f5e2b6e3e84bbbdd6ef0d82c0f84092616747da1d3fb7794ba` |
| Notes | `instance/notes/notes.db` | 61,440 | 1 | `notes=10`, `note_links=1` | `62478ea3bfacb7b359afc126081aa20e5bfaf5ad2cd31c1bad17e83ad0a4b690` | `9924659504a73e43bdc4095d13850476bcd94f9654553bdbc19fc5c8cf7dd979` | `c06882cbda8334d50b6427249c799ca571c0e1c31685534ffbb51f7f1077bfe5` |
| Publishing | `instance/publishing/publishing.db` | 73,728 | 1 | `publications=10`, `publication_versions=11` | `73e001f127cc40ae18edcbe09c4c8c1319d70d4efde07974bd3a80ff79bc32d9` | `10a92441d1925d4a97f2b0ebcf7c139e20d0e2ac31e543cdd7400859420cc355` | `d39e32f5e3e6113cfc0c311f21cfb3f783fe35eb232a81c869173faa73c65b2d` |
| Assets | `instance/assets/assets.db` | 32,768 | 1 | `assets=1`, `asset_references=5` | `0e740f0c663533440694d4f16f713f69ae30ed363626fd1e67e2a3f9bcaf53be` | `bf2d3dface8115ac60ca2b18a4366b7f134bd7586097f477451ceef8d8efe457` | `91df1532ce1e688436e3d21667b16dd873ba8fab71b2c9380528e1ed66117228` |

All four databases returned `PRAGMA quick_check = ok` and zero rows from
`PRAGMA foreign_key_check` at capture time.

| Non-DB artifact | Captured value |
|---|---|
| App Registry | `shared/apps.json`; 2,225 bytes; SHA-256 `96125ebba5a38aef6e9bb0e33cb8027baf9dc568cd9f259b892fcc2280269e3f` |
| Publication type manifest | `instance/publishing/types.json`; 471 bytes; SHA-256 `e8dee5e98f4199a0a6c4889027a494b8f9512234acfd4e79a30933a038ff2a45` |
| Asset blob inventory | `instance/assets/blobs`; 1 file; 1,146,572 bytes; path/hash/size manifest SHA-256 `776756bd8996f74f42ca22da9ec8883f09facfec734916ada3f405d943827dde` |

The registry contains two enabled internal apps (`calendar`, `notes`). It is a global immutable catalog;
future per-workspace enablement belongs in `workspace_apps`, never by mutating this registry snapshot.

## 4. Environment and network contract

### Current inputs

| Environment key | Current behavior |
|---|---|
| `CALENDAR_DB_PATH` | Calendar DB override; takes precedence over legacy `TODO_DB_PATH` |
| `TODO_DB_PATH` | Supported legacy Calendar DB override |
| `NOTES_DB_PATH` | Notes DB override |
| `PUBLISHING_DB_PATH` | Publishing DB override |
| `ASSET_DB_PATH` | Asset DB override |
| `PUBLICATION_TYPES_PATH` | Publication type manifest override |

The default paths are under `instance/`. Production configuration must use absolute paths. The common
configuration module introduced in Phase 0 is the only parser/validator; domain modules may consume its
typed settings but must retain the documented legacy precedence until compatibility removal is approved.
Missing required production values and invalid paths, booleans, durations, origins, proxy networks or
environment names must fail startup with a non-secret aggregate error. `.env.example` contains key names
and safe development defaults only—never passwords, tokens, hashes or deployable secrets.

Identity/auth configuration added in later phases must cover, without weakening the fixed plan defaults:
identity DB path, environment (`development|test|production`), public origin, cookie name/secure policy,
session idle 8 hours and absolute 7 days, invitation 24 hours, reset 30 minutes, maximum 10 active sessions,
login limit 5 failures per account+IP per 15 minutes, 5-minute `last_seen_at` write interval, trusted proxy
allowlist, and local outbox path. Production rejects an insecure cookie policy and an absent canonical HTTPS
origin. Secret values are injected at runtime and never supplied as development defaults.

### Network baseline

- Documented local start is `python -m uvicorn portal.main:app --reload`, whose default listener is
  `127.0.0.1:8000`.
- Normal Portal, Calendar, Notes, Publishing, Asset and Markdown rendering loads vendored local static
  files. FullCalendar 7.0.2, Pretendard 1.3.9, Marked 18.0.7 and DOMPurify 3.4.12 do not require runtime CDN
  requests.
- The current registry has no external app entry. The registry schema permits an external HTTPS link, but
  navigation occurs only after an explicit user click and is not a server-side dependency.
- There is no email provider or other required outbound integration. Development password-reset delivery
  remains a local outbox until a separate deployment approval.
- Trusted reverse-proxy headers are ignored unless the peer is in the configured trusted proxy allowlist.
  The single-host/single-worker SQLite premise is a release gate, not a tuning suggestion.

## 5. Test baseline and recapture

At capture, `pytest --collect-only` found **280 Python tests** and completed successfully. There are **18
Node test files**, and Chromium E2E is maintained under `tests/e2e`. Collection is inventory evidence, not
a claim that the full suite passed in this documentation lane.

Before any promote, recapture Git identity/status, all database and manifest values above, blob inventory,
the full Python/Node/Chromium result, and a network trace proving no unexpected runtime host. Store that
evidence inside the release-specific migration bundle described in
[`workspace-migration-contract.md`](workspace-migration-contract.md).

