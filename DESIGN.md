# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-08-14
- Primary product surfaces: Portal Hub, Workspace Home, shared App Shell, Calendar, Notes, Publishing, Jobs, Workspace settings
- Evidence reviewed: `shared/templates/app_shell.html`, `identity/templates/workspace_home.html`, shared tokens, Calendar and Notes templates/CSS/JS, five-viewport Chromium tests

## Brand
- Personality: calm local workspace, focused writing desk, practical planner
- Trust signals: local-only assets, predictable navigation, preserved data, explicit save state
- Avoid: decorative dashboards, dense modal forms, novelty motion, duplicated app chrome

## Product goals
- Goals: move between apps without losing context; make schedules and linked writing feel cohesive
- Non-goals: rich-text editing, novelty analytics, visual effects without functional value
- Success signals: fast recognition of current app/state, no horizontal overflow, keyboard-complete core flows

## Personas and jobs
- Primary personas: personal Workspace owners and small team members organizing shared schedules and durable knowledge
- User jobs: resume recent work, open a favorite resource, scan Workspace activity, capture a thought, prepare meeting minutes, connect research to an event
- Key contexts of use: desktop planning, compact laptop/tablet, quick mobile review/edit

## Information architecture
- Primary navigation: shared Sidebar, Header, Launcher, Registry-driven app cards
- Menu projection: every authenticated Workspace app resolves one Effective Workspace Apps snapshot and projects it through the shared shell context; app routers must not build sidebar/launcher lists independently
- Core routes/screens: `/`, `/w/{workspace_id}/home`, `/w/{workspace_id}/calendar`, `/w/{workspace_id}/notes`, `/w/{workspace_id}/publications`, `/w/{workspace_id}/jobs`, Notes new/detail states
- Content hierarchy: Workspace chrome → orientation/quick access → personal context (favorites/recent) → team activity → app work surface

## Design principles
- Writing first: Notes editor should read as a document, not a database form
- Context beside content: Calendar details use a spacious left/right planner spread on desktop
- Progressive density: desktop uses two panes; mobile uses one focused view
- Resume before browse: Workspace Home prioritizes returning to saved/recent resources over decorative metrics
- Tradeoffs: preserve existing tokens and vanilla implementation over introducing a component framework

## Visual language
- Color: existing `--workspace-*` and Calendar tokens; restrained paper/surface tints
- Typography: local Pretendard; strong document title and readable 1.75 body line-height
- Spacing/layout rhythm: 8–16px controls, 24–54px document/page margins
- Shape/radius/elevation: modest shell radii; flatter paper document; subtle elevation only
- Motion: 140–180ms state transitions, disabled with reduced motion
- Imagery/iconography: existing local SVG/CSS marks only

## Components
- Existing components to reuse: shared Shell buttons, Sidebar, Launcher, Calendar button variants
- New/changed components: Workspace Home hero, quick-access app cards, resource lists, activity timeline, Notes document surface, Note type badge, save status, Calendar task book pages, Jobs command cards, shared create/edit Job dialog, dedicated run-history dialog, archive confirmation dialog, resizable screenshot selector dialog, run timeline, Web Collection Note 정리 방식 selector, workspace formatter management dialog
- Variants and states: selected, pinned, loading, saving, empty, error, orphan; Jobs create/edit, run-history, formatter management, and screenshot preview dialogs keep orientation/header/actions fixed while long content scrolls; deleting a run refreshes the open history dialog in place; archive, archived-only permanent deletion, terminal run-history deletion, unused formatter deletion, and built-in financial formatter reset remain explicit, distinct confirmation steps; setting tests return immediately after queueing, continue through the Worker in the background, and appear in history as `설정 테스트` without exposing collection retry controls; Web Collection defaults to raw Markdown, exposes the built-in AI 금융시장 브리핑 as a Workspace-overridable managed profile, and lists workspace-scoped custom formatter profiles without ever rendering or storing the API key
- Token/component ownership: shell tokens remain shared; Notes and Calendar styling remains app-local

## Accessibility
- Target standard: WCAG 2.1 AA behavior and contrast
- Keyboard/focus behavior: visible focus, list arrow navigation, deterministic mobile focus return, dialog DOM order left-to-right
- Contrast/readability: token-based text colors, 16px body/editor text
- Screen-reader semantics: active app, listbox/option state, page headings, live save/link status
- Reduced motion and sensory considerations: transitions removed under `prefers-reduced-motion`

## Responsive behavior
- Supported breakpoints/devices: 320, 375, 768, 1024, 1440
- Layout adaptations: Notes list/editor swap under 760px; Calendar book collapses under 860px; bottom sheet under 520px
- Touch/hover differences: minimum 42–44px actions; hover is supplemental

## Interaction states
- Loading: every shared-shell screen shows the same local SVG/CSS progress indicator without layout shift; server-rendered screens dismiss it after first paint, while async apps dismiss it only after initial data settles; a Job test button shows only the short queue-registration busy state, then restores controls while an aria-live status follows Worker progress for up to 240 seconds
- Empty: clear next action and focusable creation CTA
- Error: persistent accessible local alert; a failed Web Collection page preview keeps its modal open and places a focused `다시 시도` action beside the failure instead of forcing the user back through the form
- Success: inline and screen-reader save status
- Disabled: saving action disabled with explicit label
- Offline/slow network: local server remains source of truth; stale responses must not replace newer state

## Content voice
- Tone: concise, calm, action-oriented Korean
- Terminology: Note/노트, 일정, 회의록, 연구 노트, 연결 해제
- Microcopy rules: state what happened and the next available action; avoid technical DB language in UI; Web Collection Note titles use `작업 제목 - YYYY-MM-DD HH:MM:SS` with an Asia/Seoul timestamp so repeated captures remain distinguishable

## Implementation constraints
- Framework/styling system: Jinja, CSS, vanilla ES modules
- Design-token constraints: reuse shared tokens; no new dependency or external asset
- Performance constraints: no runtime external requests; request generation guards for async UI
- Compatibility constraints: existing API/static routes, Registry and pin contracts remain unchanged
- Test/screenshot expectations: Python, Node, Chromium, five viewports, external/failed/console zero

## Open questions
- [ ] Evaluate a dedicated mobile linked-source drawer only if multiple source providers make the inline context too dense.
