# Workspace authorization threat model

> Applies to authentication, workspace membership, app policy, Notes, Calendar, Publishing, Assets,
> Search, Dashboard, internal links and later AI providers. The security boundary is the backend; hidden
> navigation and buttons are UX only.

## 1. Protected assets and trust boundaries

### Data classification

| Class | Examples | Required handling |
|---|---|---|
| Secret | Password input, Argon2id hash, session/CSRF/invitation/reset token, provider credential | Never log or return; store only hashes where specified; redact error details; runtime injection only |
| Restricted identity/security | Email, user/session records, membership, role, override, audit security event, IP/rate-limit key | Authenticated least-privilege access; retention and audit policy; no cross-workspace disclosure |
| Workspace confidential | Notes content, Calendar details, internal publication/draft/version, internal asset/blob, favorites, recent views, search indexes | Workspace plus app plus permission filter on every read/write and derived projection |
| Public by explicit policy | Published immutable snapshot and only its resolved public assets | Anonymous access only through random `public_id` or preserved approved alias; workspace must remain active |
| Operational | Schema/release/checksum/row-count manifest, request ID, non-sensitive health evidence | Integrity protected; avoid embedding content, token, email or filesystem secret |

### Trust boundaries

1. Browser input crosses into FastAPI. URL identifiers, headers, body `user_id`/`workspace_id`, cookies,
   query strings and filenames are untrusted.
2. Authentication resolves the user only from a verified opaque session. A client workspace header is not
   an identity source.
3. Canonical URL `workspace_id` crosses into `WorkspaceContext`; active membership is rechecked on every
   request. `last_workspace_id` is redirect convenience only.
4. `AuthorizationService` is the single decision point. Endpoint and domain code do not reimplement role
   matrices or override precedence.
5. Four domain databases cross an application-enforced boundary with the Identity DB. Cross-DB FK,
   `ATTACH`, join and transaction are prohibited.
6. Public publication and asset endpoints cross from anonymous traffic into explicitly projected immutable
   public data. Internal source databases are not directly exposed through that boundary.

## 2. Authorization invariant

Every protected operation follows this order:

```text
verified session
→ active workspace and active membership
→ workspace app enabled
→ explicit membership DENY
→ explicit membership ALLOW
→ role permission
→ OWN comparison with server-read resource owner
```

`DENY` wins over `ALLOW` and role permission. `MANAGE` is independent and does not imply CRUD. An OWN
decision never trusts an owner ID supplied by the client. Repository methods require `workspace_id` and
resource ID together and exclude soft-deleted rows unless executing an authorized restore. Cross-workspace
or non-member resource access is indistinguishable from absence (`404`); same-workspace insufficient
permission is `403`.

## 3. Threat inventory and mandatory controls

| ID | Threat / abuse case | Required control and verification |
|---|---|---|
| T01 | IDOR by replacing `workspace_id`, note/task/publication/asset ID or link target | Canonical URL workspace, active membership, workspace-scoped repository predicates; matrix tests across every domain return `404` and never disclose title/count |
| T02 | Endpoint omits authorization or implements a divergent role table | FastAPI coarse dependencies plus injected `AuthorizationService`; architecture test rejects direct role/override evaluation outside `access`; all legacy adapters call the same service |
| T03 | Client claims another `user_id`, owner or workspace through body/header | Resolve user from session and workspace from URL; ignore/reject identity claims; OWN uses server-read `created_by` |
| T04 | Disabled app remains callable through a direct URL/API | `authorize_app` precedes domain permission and is shared by UI projection and API; disabling takes effect on the next request |
| T05 | Explicit DENY bypass through role permission, cache or legacy route | Fixed `DENY > ALLOW > role` order; cache key includes authorization version; mutation invalidates next request; canonical/legacy parity tests |
| T06 | Last Owner is removed, disabled or displaced in a race | `BEGIN IMMEDIATE`; count ACTIVE Owners inside the transaction; return `409`; concurrent leave/demote/delete/disable tests |
| T07 | Admin grants Owner capability, demotes Owner or creates equivalent custom role | Owner-only catalog flag, immutable built-ins, grant-subset rule; service and DB validation; audit event |
| T08 | Old override revives after leave and rejoin | Overrides key to membership ID; new membership row on rejoin; no workspace/user global membership reuse; lifecycle test |
| T09 | Session theft/fixation, stale privilege or excessive lifetime | CSPRNG opaque token, DB SHA-256 token hash, HttpOnly/SameSite cookie, production Secure, login rotation, idle+absolute TTL, revoke on logout/password/disable, re-resolve membership per request |
| T10 | CSRF performs a mutating request | Session-bound CSRF header plus same-origin Origin/Referer validation on unsafe methods; missing/wrong/cross-origin tests return `403` |
| T11 | Password brute force or account enumeration | Argon2id fixed production cost, account+IP rate limit, generic reset response, normalized email, deterministic test clock; security audit without password/token |
| T12 | Search, Dashboard, favorites, recent views or counts leak inaccessible data | Providers receive immutable WorkspaceContext and apply current permission/deletion filters before result/count/pagination; hostile mixed-tenant fixtures |
| T13 | Internal link/source resolver crosses tenant because it accepts a global integer ID | Add workspace to every reference/provider contract; foreign workspace resolves `exists=false`; no cross-DB join |
| T14 | Public slug enumeration exposes another tenant or an INTERNAL draft | Random immutable `public_id`; aliases only from migration manifest; explicit `PUBLIC + published + active workspace`; all other states `404` |
| T15 | Public asset endpoint becomes a general blob oracle | Immutable published-version asset projection; publication and asset workspace equality; asset must be in resolved projection; no raw storage key endpoint |
| T16 | Global SHA dedup reveals another workspace's asset or controls its lifecycle | Unique `(workspace_id, sha256)`; uploader/workspace metadata; tenant-scoped resolve/reconcile/delete |
| T17 | Soft-deleted workspace/resource remains reachable through old route, search or cache | Active workspace and `deleted_at IS NULL` are common predicates; immediate cache version change; public alias also returns `404` |
| T18 | Multi-tab workspace confusion uses session “current workspace” for data access | Canonical URL is authoritative and each request creates its own context; E2E with two tabs in different workspaces |
| T19 | Logs/errors/manifests disclose content, PII or secrets | Structured redaction; stable public error body and request ID; manifest stores hashes/counts, not content/token/password; log scanning tests |
| T20 | Migration assigns legacy rows to a guessed user or mixes database releases | Explicit bootstrap Owner, staging-only migration, coordinated release manifest, write fence, mixed-release startup refusal, all-DB rollback set |
| T21 | Broken cross-DB references create authorization gaps | Verification requires zero unexpected orphan actor/workspace/source/asset references; allowed snapshot orphan has explicit count and reason |
| T22 | Malicious asset path or upload escapes blob root | Server-generated storage keys, normalized safe path, content and size validation, no client filesystem path, traversal tests |
| T23 | Reverse-proxy spoof changes client IP/origin/security policy | Ignore forwarded headers unless immediate peer matches explicit trusted proxy allowlist |
| T24 | External runtime dependency/CDN exfiltrates content or makes auth unavailable | Vendored static assets and no required outbound runtime network; browser/server host allowlist verification |
| T25 | SQLite multi-writer assumptions cause lost Owner/auth/migration state | Single host + single Uvicorn worker gate; `BEGIN IMMEDIATE` for invariants; stop and approve PostgreSQL project before scaling |

## 4. Security response and audit contract

Errors use `{code, message, request_id, details?}`. Details are allowlisted and never contain a password,
token, hash, content body, storage path or a foreign resource's existence. Authentication absence is `401`,
CSRF and same-workspace permission denial are `403`, hidden/cross-workspace resources are `404`, and owner
invariant or concurrent state conflicts are `409`.

Audit records security and administrative facts separately from user-facing Activity. Required audit events
include login/logout/failure/rate-limit, account disable, ownership and membership changes, invitations,
role/override changes, workspace app changes, workspace delete, public visibility change and migration
promote/rollback. Metadata is minimal and redacted. Audit reads are themselves workspace- and
permission-scoped, except narrowly defined platform operations.

## 5. Security gates

Implementation cannot advance past a domain cutover when any of these is observed:

- one cross-workspace read/write, auth bypass, explicit DENY bypass or INTERNAL anonymous disclosure;
- one ID-only workspace-aware repository method or one provider that accepts no WorkspaceContext;
- ability to remove the final active Owner or grant Owner-only capability as Admin;
- password, token or secret in Git, stdout, shell arguments/history, logs, error details or a manifest;
- mixed DB release accepted at startup, unexplained orphan, checksum/content/row loss, or migration of a
  production database without a staging copy;
- production E2E pointed at operational database/blob paths;
- an unexpected outbound runtime host, untrusted forwarded header, multiple application workers, shared
  SQLite across instances, or unexplained worktree change.

Passing UI hiding tests cannot waive a backend gate. Passing happy-path API tests cannot waive hostile
workspace matrices, concurrency tests, public projection tests, migration audits or log/manifest scanning.

