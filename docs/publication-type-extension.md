# Publication Type Extension

## Built-in providers

Manual and Wiki are Python providers under `publishing/types/`. The immutable
`PublicationTypeRegistry` validates every provider at startup:

- canonical unique `type_id`
- unique local `route_prefix`
- unique deterministic `order`
- existing local icon and template
- `SlugPolicy`
- required hook signatures
- valid immutable `RenderOptions`

Publishing Core selects providers through the Registry and contains no
Manual/Wiki rendering branches.

## Declarative type management

Open `/publications/types/` from the Publications home. The page can create,
read, update, and delete safe declarative types backed by
`instance/publishing/types.json`.

Declarative fields:

- `type_id`
- `display_name`
- `order`
- `enabled`
- `toc_enabled`
- `related_enabled`
- `version_visible`

Changes are written atomically, the complete provider set is validated again,
and the Publication Type Registry swaps its internal snapshot in one operation.
No Workspace restart is required. The Workspace App Registry remains a separate
immutable object and is never mutated by this flow.

Built-in providers are read-only. A custom type with publications cannot be
deleted; disable it instead. This preserves existing public content and
prevents startup from encountering an unknown stored type.

Declarative types intentionally use the shared publication viewer, local
generic icon, standard slug policy, default search provider, and no custom
database access. Their stable public route is
`/publications/view/{type_id}/{slug}`. This keeps UI management safe and bounded
while allowing a newly registered type to work immediately without mutating the
FastAPI route table at runtime.

## Python provider extension

Types requiring custom behavior implement the `PublicationType` protocol:

- identity: `type_id`, `display_name`, `icon`, `order`, `enabled`
- routing: `route_prefix`, `template_name`, `slug_policy`
- rendering: `render_options()`, `build_view_context()`
- validation: `validate_publish()`
- optional extension points: `navigation_provider()`, `search_provider()`,
  `related_content_provider()`

Optional hooks return `None` by default. Providers may build view context
through official services but must not open unrelated app databases or create
cross-database joins. Templates render the returned context; providers do not
return unsanitized HTML.

Register a provider explicitly in the Portal composition root. Filesystem
discovery, import side effects, and App Registry coupling are deliberately
forbidden.

## MVP exclusions

- arbitrary Python provider installation through the browser
- filesystem auto-discovery
- plugin runtime
- provider-specific schema migrations
- Manual navigation hierarchy
- Wiki backlinks
- slug aliases and redirects
