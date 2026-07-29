# Design

## Source of truth
- Status: Active
- Last refreshed: 2026-07-29
- Primary product surfaces: Portal Hub, shared App Shell, Calendar, Notes
- Evidence reviewed: `shared/templates/app_shell.html`, shared tokens, Calendar and Notes templates/CSS/JS, five-viewport Chromium tests

## Brand
- Personality: calm local workspace, focused writing desk, practical planner
- Trust signals: local-only assets, predictable navigation, preserved data, explicit save state
- Avoid: decorative dashboards, dense modal forms, novelty motion, duplicated app chrome

## Product goals
- Goals: move between apps without losing context; make schedules and linked writing feel cohesive
- Non-goals: rich-text editing, collaboration, authentication, visual effects without functional value
- Success signals: fast recognition of current app/state, no horizontal overflow, keyboard-complete core flows

## Personas and jobs
- Primary personas: a single local Workspace user organizing schedules and durable notes
- User jobs: capture a thought, prepare meeting minutes, connect research to an event, recover context after an event is deleted
- Key contexts of use: desktop planning, compact laptop/tablet, quick mobile review/edit

## Information architecture
- Primary navigation: shared Sidebar, Header, Launcher, Registry-driven app cards
- Core routes/screens: `/`, `/apps/calendar/`, `/apps/notes/`, Notes new/detail states
- Content hierarchy: Workspace chrome → app toolbar → primary work surface → contextual actions

## Design principles
- Writing first: Notes editor should read as a document, not a database form
- Context beside content: Calendar details use a spacious left/right planner spread on desktop
- Progressive density: desktop uses two panes; mobile uses one focused view
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
- New/changed components: Notes document surface, Note type badge, save status, Calendar task book pages
- Variants and states: selected, pinned, loading, saving, empty, error, orphan
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
- Loading: local status text without layout shift
- Empty: clear next action and focusable creation CTA
- Error: persistent accessible local alert
- Success: inline and screen-reader save status
- Disabled: saving action disabled with explicit label
- Offline/slow network: local server remains source of truth; stale responses must not replace newer state

## Content voice
- Tone: concise, calm, action-oriented Korean
- Terminology: Note/노트, 일정, 회의록, 연구 노트, 연결 해제
- Microcopy rules: state what happened and the next available action; avoid technical DB language in UI

## Implementation constraints
- Framework/styling system: Jinja, CSS, vanilla ES modules
- Design-token constraints: reuse shared tokens; no new dependency or external asset
- Performance constraints: no runtime external requests; request generation guards for async UI
- Compatibility constraints: existing API/static routes, Registry and pin contracts remain unchanged
- Test/screenshot expectations: Python, Node, Chromium, five viewports, external/failed/console zero

## Open questions
- [ ] Evaluate a dedicated mobile linked-source drawer only if multiple source providers make the inline context too dense.
