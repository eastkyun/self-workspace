# Job Platform

The Job Platform owns durable definitions, schedules, run claims, leases,
retries, failure counters, and generic history. Type adapters own all
type-specific data and execution. The core package must not import Playwright,
Notes, Markdown conversion, or a concrete Job Type.

## Current implementation boundary

The schema-v1 Job Core, interval/cron calculator, registry, revision-bound test
gate, due-run deduplication, claim/heartbeat/fencing, stale recovery, retry
lineage, failure auto-pause, typed Web Collection rows, generic Jobs API/UI, and
the standalone CLI entrypoint are implemented. Notes schema v3 now provides a
Workspace-scoped durable idempotency key, concurrent retry deduplication, and a
stopped-service v2-to-v3 promotion command. `WEB_COLLECTION` execution and
selector testing remain disabled until the browser runtime and production
egress controls are completed and verified.

Before enabling Jobs against an existing Notes v2 database, stop every process
that can access Notes and run:

```bash
WORKSPACE_SINGLE_WORKER_ASSERTED=true \
  .venv/bin/python -m tools.workspace_cli upgrade-notes-v3 \
  --database instance/notes/notes.db
```

The command prepares and verifies a sibling staging copy, rejects live WAL
state or a changed source, atomically promotes v3, retains a timestamped v2
backup, and restores that backup if post-promotion verification fails.

## Commands

Initialize the explicitly configured Jobs database before enabling the runtime:

```bash
.venv/bin/python -m tools.jobs initialize
.venv/bin/python -m tools.jobs verify
.venv/bin/python -m tools.jobs health
```

The scheduler entrypoint is:

```bash
.venv/bin/python -m tools.jobs run-due
```

It fails closed unless both `JOBS_ENABLED` and
`JOBS_WEB_COLLECTION_ENABLED` are true. A timer may call it once per minute only
after the remaining browser release gates pass.

## Phase 0 evidence

- Playwright lifecycle and deterministic geometry fixture: passed by the
  original implementation spike.
- Cron and DST: `croniter` is bounded to five-field expressions. Nonexistent
  and ambiguous local times are skipped deterministically; golden tests cover
  both US DST transitions.
- URL policy: only canonical HTTP(S) on ports 80/443 is accepted. Every mixed
  DNS answer is rejected if any address is not globally routable. The same
  policy object is intended for initial navigation, redirects, and all browser
  subresources.
- Markdown conversion: `markdownify` was selected after a golden fixture kept
  headings, lists, tables, code, and safe relative links while active elements
  and unsafe schemes were removed.
- Chunked transfer accounting, browser request routing, popup/download/socket
  blocking, and context cleanup remain browser-manager work.
- Production egress and CPU/RAM limits require operator-controlled firewall or
  proxy and service limits. Application configuration therefore defaults both
  `JOBS_ENABLED` and `JOBS_WEB_COLLECTION_ENABLED` to `false`.

This is a bounded fallback, not evidence that production browser isolation is
complete. Do not enable Web Collection until the remaining release gates pass.
