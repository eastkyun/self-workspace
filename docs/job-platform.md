# Job Platform

The Job Platform owns durable definitions, schedules, run claims, leases,
retries, failure counters, and generic history. Type adapters own all
type-specific data and execution. The core package must not import Playwright,
Notes, Markdown conversion, or a concrete Job Type.

## Current implementation boundary

The schema-v1 Job Core, interval/cron calculator, registry, revision-bound test
gate, due-run deduplication, claim/heartbeat/fencing, stale recovery, retry
lineage, failure auto-pause, typed Web Collection rows, generic Jobs API/UI, and
the standalone CLI entrypoint are implemented. The worker now persists bounded
collection results, compares normalized Markdown hashes, applies
`CHANGED_ONLY`/`EVERY_RUN`, and creates Notes with a run-scoped idempotency key.
Generated Note titles retain the Job name and append a second-precision
`YYYY-MM-DD HH:MM:SS` timestamp in `Asia/Seoul`.
Failed or uncertain Note-result writes are reconciled against the original run
key before a later unchanged collection can suppress output. Notes schema v3 provides a
Workspace-scoped durable idempotency key, concurrent retry deduplication, and a
stopped-service v2-to-v3 promotion command. Browser-backed execution remains
behind both kill switches and must not be enabled in production until the
remaining host egress and resource-control gates are verified.

The creation UI can request a bounded JPEG preview and select from a server-
generated hit-map. Remote HTML is never returned to or rendered by the
Workspace browser. Preview geometry is capped at 1,920×3,600 CSS pixels, the
candidate list at 1,000 unique selectors, and the encoded JPEG at 2 MiB. Preview
navigation blocks media, keeps a bounded 96 MiB transfer budget, and
uses a separate 40-second operation budget with a 20-second screenshot cap;
normal collection blocks font/image/media payloads and retains the 96 MiB hard
cap with the shorter 20-second operation limit. Extracted HTML and text
remain capped at 1 MiB and 100 KiB respectively. Before those caps are applied,
the selected DOM clone drops scripts, SVG/canvas/media, forms, and presentation
attributes while preserving text, link targets, image alt text, and table
semantics used by Markdown conversion. Once the selected element or preview
hit-map is available, navigation is stopped before extraction/screenshot so
background SPA requests cannot consume the remaining transfer budget.
Preview waits for a bounded DOM/text/layout stability window, while collection
uses a fixed 1.5-second initial render window plus at most 2.5 seconds for the
selected element. This captures delayed values without waiting indefinitely on
live tickers or polling pages. Preview overlays are placed
inside the exact screenshot-sized stage rather than the scroll viewport, so
their percentage geometry stays aligned while the modal is resized.
Browser operations retain a concurrency limit of two, but callers now wait up
to 15 seconds for a slot before receiving `BROWSER_SATURATED`; the Jobs UI maps
that and other expected browser codes to actionable Korean messages.
Worker startup installs a database trigger for its numeric runtime version,
marks older running invocations as superseded, and prevents a stale process
from claiming newly queued runs. This avoids old in-memory browser limits racing
the current Worker after a development restart.
Job setting tests are queued as durable background runs and executed by the
standalone Worker rather than inside the web request. A test run never creates a
Note, increments collection failure counters, triggers automatic pause, or
creates collection retry lineage. The existing schema identifies these internal
test runs with a `test:` deduplication-key prefix while retaining `MANUAL` as the
stored trigger kind.

Web Collection definitions can choose either raw Markdown or an AI financial
market briefing. The latter sends only the selected, bounded Markdown plus its
source and KST collection time to the OpenAI Responses API from the Worker,
using the repository-owned formatter prompt. It fails closed when
`OPENAI_API_KEY` is absent or the response does not match the expected Markdown
heading; it never falls back to an unformatted Note. The key is process-only and
is not accepted by the Jobs API, stored in SQLite, or rendered in the UI. The
default model is `gpt-5.6-luna` and can be overridden with
`JOBS_OPENAI_MODEL`. The default AI request timeout is 180 seconds, and the UI
follows a queued run for up to 240 seconds without keeping the enqueue request
open, leaving room for browser collection and queue pickup.

Workspace members can also manage reusable custom formatter profiles from the
Jobs screen. A profile stores only a display name, trusted formatting
instructions, and model name; the API key remains Worker process configuration.
Profiles are tenant-scoped and revision-checked. Updating a profile pauses each
active dependent Job, increments its definition revision, and requires a new
setting test. A profile that is still referenced by any Job cannot be deleted.
The repository-owned financial-market formatter appears in the same management
dialog as a built-in profile. Each Workspace can override its name,
instructions, and model without modifying source files, then restore the
repository default. Updating or restoring it invalidates every dependent
built-in financial briefing Job under the same safety rules.

Each Job card exposes revision-bound testing, activation, pause, manual queueing,
definition editing, and on-demand run history in a dedicated modal. Editing an
active definition pauses it and resets the test gate. Archiving preserves history; only an archived
Job exposes separately confirmed permanent deletion, which cascades through its
runs and type-owned collection results. Actions are CSRF protected and disable
their sibling controls while pending. History uses KST display timestamps and
links successful collection results to the generated Workspace Note.
Manual run actions show an explicit in-progress label while duplicate/conflicting
card actions are locked, then restore every control to its original state after
terminal completion, failure, or polling timeout.
Terminal history rows expose a compact minus action with explicit confirmation.
Deleting an original run also removes its terminal retry descendants and their
type-owned results while preserving already-created Notes; any chain containing
a `QUEUED` or `RUNNING` run is rejected. A successful deletion refreshes the
open history modal in place rather than collapsing or redrawing the Job card.

`FAILED`/`PARTIAL` history rows on an active Job expose an authorized retry
action. A retry always creates a new `RETRY` run linked to the original run,
rejects stale definitions and overlapping work, and follows the new run in the
UI. Web Collection reuses an existing pending Note result when possible; if the
failed attempt produced no collection result, it safely recollects the target.

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

한 번만 due queue를 처리하는 scheduler entrypoint는 다음과 같습니다.

```bash
.venv/bin/python -m tools.jobs run-due
```

It fails closed unless both `JOBS_ENABLED` and
`JOBS_WEB_COLLECTION_ENABLED` are true. A production timer may call it once per
minute only after the operator-controlled egress and resource gates pass.

로컬 개발 환경에서 지속적으로 queue를 처리하려면 별도 worker 프로세스를
실행합니다. worker는 기본 30초마다 due schedule과 수동 실행 queue를 확인하고,
SIGINT/SIGTERM을 받으면 현재 cycle을 마친 뒤 browser를 닫고 `STOPPED` 상태를
기록합니다.

```bash
.venv/bin/python -m tools.jobs worker --poll-seconds 30 --limit 50
```

두 kill switch가 활성화된 경우 `./start.sh`는 Uvicorn과 이 worker를 함께 시작하고
한 프로세스가 종료되면 나머지 프로세스에도 종료 신호를 전달합니다. 운영에서는
웹 프로세스와 worker를 별도 service로 관리해야 하며, outbound firewall/proxy와
browser CPU/RAM 제한을 검증하기 전에는 Web Collection을 활성화하지 않습니다.
systemd 지속 Worker, systemd timer, cron, Windows 작업 스케줄러 예시와 운영
release gate는 [Jobs 운영 가이드](jobs-operations.md)를 따릅니다.

Jobs 화면의 Worker 상태는 최근 heartbeat를 기준으로 `정상`, `응답 없음`, `오류`,
`중지됨`, `미실행` 중 하나를 표시합니다. API 응답에는 경로, URL, 수집 콘텐츠,
raw exception을 포함하지 않습니다.

## Phase 0 evidence

- Playwright lifecycle and deterministic geometry fixture: passed by the
  original implementation spike.
- Cron and DST: `croniter` is bounded to five-field expressions. Nonexistent
  and ambiguous local times are skipped deterministically; golden tests cover
  both US DST transitions.
- URL policy: only canonical HTTP(S) on ports 80/443 is accepted. Every mixed
  DNS answer is rejected if any address is not globally routable. The same
  policy object revalidates initial navigation, redirects, and browser
  subresources at the route boundary.
- Markdown conversion: `markdownify` was selected after a golden fixture kept
  headings, lists, tables, code, and safe relative links while active elements
  and unsafe schemes were removed.
- Browser manager: CDP encoded-byte accounting enforces the transfer cap while
  requests are in flight; routing blocks disallowed destinations, sockets, and
  configured heavy resource types. Popup/download cancellation, crash reset,
  concurrency saturation, and 20 consecutive isolated-context cleanup are
  covered by deterministic Chromium tests.
- Production egress and CPU/RAM limits require operator-controlled firewall or
  proxy and service limits. Application configuration therefore defaults both
  `JOBS_ENABLED` and `JOBS_WEB_COLLECTION_ENABLED` to `false`.

This is a bounded fallback, not evidence that production browser isolation is
complete. Do not enable Web Collection until the remaining release gates pass.
