from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
PORTAL_DIR = WORKSPACE_ROOT / "portal"
PORTAL_STATIC_DIR = PORTAL_DIR / "static"
REGISTRY_PATH = Path(__file__).resolve().with_name("apps.json")
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})

_SLUG_PATTERN = r"^[a-z][a-z0-9-]*$"
_VERSION_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"


def normalize_internal_route(route: str) -> str:
    """Return the canonical internal app route.

    Internal app routes have no query or fragment and always end in exactly
    one slash. Consequently, `/apps/calendar` and `/apps/calendar/` identify
    the same route.
    """

    parsed = urlsplit(route)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("internal app route must be a path without query or fragment")
    if not parsed.path.startswith("/apps/"):
        raise ValueError("internal app route must start with /apps/")
    if "//" in parsed.path or any(
        segment in {".", ".."} for segment in parsed.path.split("/")
    ):
        raise ValueError("internal app route must be a canonical path")
    path = parsed.path.rstrip("/")
    if path == "/apps":
        raise ValueError("internal app route must identify an app")
    return f"{path}/"


class RegistryIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    source: str
    app_id: str | None = None
    field: str | None = None
    message: str


class RegistryValidationError(ValueError):
    def __init__(self, issues: list[RegistryIssue] | tuple[RegistryIssue, ...]):
        self.issues = tuple(
            sorted(
                issues,
                key=lambda issue: (
                    issue.source,
                    issue.app_id or "",
                    issue.field or "",
                    issue.code,
                    issue.message,
                ),
            )
        )
        details = "\n".join(
            f"- [{issue.code}] {issue.source}"
            f"{f' app={issue.app_id}' if issue.app_id else ''}"
            f"{f' field={issue.field}' if issue.field else ''}: {issue.message}"
            for issue in self.issues
        )
        super().__init__(f"app registry validation failed:\n{details}")


class AppCategory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=_SLUG_PATTERN)
    display_name: str = Field(min_length=1, max_length=60)
    order: int = Field(ge=0)
    description: str = Field(default="", max_length=160)


class AppManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=_SLUG_PATTERN)
    name: str = Field(pattern=_SLUG_PATTERN)
    display_name: str = Field(min_length=1, max_length=60)
    description: str = Field(max_length=160)
    icon: str
    route: str
    category: str = Field(pattern=_SLUG_PATTERN)
    enabled: bool
    default_pinned: bool
    navigation_visible: bool
    launcher_visible: bool
    order: int = Field(ge=0)
    version: str = Field(pattern=_VERSION_PATTERN)
    kind: Literal["internal", "external"] = "internal"
    search_keywords: tuple[str, ...] = ()

    @field_validator("icon")
    @classmethod
    def validate_icon(cls, icon: str) -> str:
        parsed = urlsplit(icon)
        if (
            not icon.startswith("/static/icons/")
            or parsed.query
            or parsed.fragment
            or ".." in parsed.path.split("/")
        ):
            raise ValueError("app icon must be a local /static/icons/ asset")
        return icon

    @field_validator("search_keywords")
    @classmethod
    def normalize_search_keywords(cls, keywords: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(" ".join(keyword.split()) for keyword in keywords if keyword.strip())

    @model_validator(mode="after")
    def validate_and_normalize_route(self) -> "AppManifest":
        if self.kind == "internal":
            canonical_route = normalize_internal_route(self.route)
        else:
            parsed = urlsplit(self.route)
            if parsed.scheme != "https" or not parsed.netloc:
                raise ValueError("external app route must use https://")
            canonical_route = self.route
        if canonical_route != self.route:
            return self.model_copy(update={"route": canonical_route})
        return self


class RegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int
    categories: tuple[AppCategory, ...]
    apps: tuple[AppManifest, ...]


def _validation_issues(
    error: ValidationError,
    *,
    source: str,
    app_id: str | None = None,
) -> list[RegistryIssue]:
    issues: list[RegistryIssue] = []
    for detail in error.errors(include_url=False):
        field = ".".join(str(part) for part in detail["loc"]) or None
        issues.append(
            RegistryIssue(
                code=str(detail["type"]),
                source=source,
                app_id=app_id,
                field=field,
                message=str(detail["msg"]),
            )
        )
    return issues


def parse_registry_payload(
    payload: Any,
    *,
    source: str,
    asset_root: Path = PORTAL_STATIC_DIR,
) -> RegistryDocument:
    issues: list[RegistryIssue] = []
    if not isinstance(payload, dict):
        raise RegistryValidationError(
            [
                RegistryIssue(
                    code="document_type",
                    source=source,
                    message="registry document must be a JSON object",
                )
            ]
        )

    allowed_document_fields = {"schema_version", "categories", "apps"}
    for field in sorted(set(payload) - allowed_document_fields):
        issues.append(
            RegistryIssue(
                code="extra_forbidden",
                source=source,
                field=field,
                message="unknown registry document field",
            )
        )

    schema_version = payload.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        issues.append(
            RegistryIssue(
                code="unsupported_schema_version",
                source=source,
                field="schema_version",
                message=(
                    f"supported versions are {sorted(SUPPORTED_SCHEMA_VERSIONS)}; "
                    f"received {schema_version!r}"
                ),
            )
        )

    raw_categories = payload.get("categories")
    if not isinstance(raw_categories, list):
        issues.append(
            RegistryIssue(
                code="list_type",
                source=source,
                field="categories",
                message="categories must be an array",
            )
        )
        raw_categories = []

    categories: list[AppCategory] = []
    for index, raw_category in enumerate(raw_categories):
        try:
            categories.append(AppCategory.model_validate(raw_category))
        except ValidationError as error:
            issues.extend(
                _validation_issues(
                    error,
                    source=f"{source}#categories[{index}]",
                )
            )

    raw_apps = payload.get("apps")
    if not isinstance(raw_apps, list):
        issues.append(
            RegistryIssue(
                code="list_type",
                source=source,
                field="apps",
                message="apps must be an array",
            )
        )
        raw_apps = []

    apps: list[AppManifest] = []
    for index, raw_app in enumerate(raw_apps):
        app_id = raw_app.get("id") if isinstance(raw_app, dict) else None
        try:
            apps.append(AppManifest.model_validate(raw_app))
        except ValidationError as error:
            issues.extend(
                _validation_issues(
                    error,
                    source=f"{source}#apps[{index}]",
                    app_id=app_id if isinstance(app_id, str) else None,
                )
            )

    category_ids: dict[str, AppCategory] = {}
    category_orders: dict[int, AppCategory] = {}
    for category in categories:
        if category.id in category_ids:
            issues.append(
                RegistryIssue(
                    code="duplicate_category_id",
                    source=source,
                    field="categories",
                    message=f"category ID is duplicated: {category.id}",
                )
            )
        else:
            category_ids[category.id] = category
        if category.order in category_orders:
            issues.append(
                RegistryIssue(
                    code="duplicate_category_order",
                    source=source,
                    field="categories",
                    message=(
                        f"category order {category.order} is shared by "
                        f"{category_orders[category.order].id} and {category.id}"
                    ),
                )
            )
        else:
            category_orders[category.order] = category

    app_ids: dict[str, AppManifest] = {}
    routes: dict[str, AppManifest] = {}
    app_orders: dict[tuple[str, int], AppManifest] = {}
    for app in apps:
        if app.id in app_ids:
            issues.append(
                RegistryIssue(
                    code="duplicate_app_id",
                    source=source,
                    app_id=app.id,
                    field="id",
                    message=f"app ID is duplicated: {app.id}",
                )
            )
        else:
            app_ids[app.id] = app

        route_key = (
            normalize_internal_route(app.route)
            if app.kind == "internal"
            else app.route
        )
        if route_key in routes:
            issues.append(
                RegistryIssue(
                    code="duplicate_route",
                    source=source,
                    app_id=app.id,
                    field="route",
                    message=(
                        f"route is shared by {routes[route_key].id} and {app.id}: "
                        f"{route_key}"
                    ),
                )
            )
        else:
            routes[route_key] = app

        if app.category not in category_ids:
            issues.append(
                RegistryIssue(
                    code="unknown_category",
                    source=source,
                    app_id=app.id,
                    field="category",
                    message=f"category is not declared: {app.category}",
                )
            )

        order_key = (app.category, app.order)
        if order_key in app_orders:
            issues.append(
                RegistryIssue(
                    code="duplicate_app_order",
                    source=source,
                    app_id=app.id,
                    field="order",
                    message=(
                        f"order {app.order} in category {app.category} is shared by "
                        f"{app_orders[order_key].id} and {app.id}"
                    ),
                )
            )
        else:
            app_orders[order_key] = app

        icon_path = asset_root / app.icon.removeprefix("/static/")
        if not icon_path.is_file():
            issues.append(
                RegistryIssue(
                    code="missing_icon",
                    source=source,
                    app_id=app.id,
                    field="icon",
                    message=f"local app icon does not exist: {app.icon}",
                )
            )

        if app.default_pinned and not app.enabled:
            issues.append(
                RegistryIssue(
                    code="disabled_default_pinned",
                    source=source,
                    app_id=app.id,
                    field="default_pinned",
                    message="a disabled app cannot be pinned by default",
                )
            )

    if issues:
        raise RegistryValidationError(issues)

    return RegistryDocument(
        schema_version=int(schema_version),
        categories=tuple(categories),
        apps=tuple(apps),
    )


class JsonRegistrySource:
    def __init__(
        self,
        path: Path = REGISTRY_PATH,
        *,
        asset_root: Path = PORTAL_STATIC_DIR,
    ) -> None:
        self.path = path
        self.asset_root = asset_root
        self.source_name = str(path)

    def load(self) -> RegistryDocument:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RegistryValidationError(
                [
                    RegistryIssue(
                        code="source_error",
                        source=self.source_name,
                        message=str(error),
                    )
                ]
            ) from error
        return parse_registry_payload(
            payload,
            source=self.source_name,
            asset_root=self.asset_root,
        )


class RegistrySource(Protocol):
    source_name: str

    def load(self) -> RegistryDocument: ...


class AppRegistry:
    """Immutable, validated snapshot of all registered workspace apps."""

    __slots__ = ("_apps", "_categories", "_app_by_id", "_category_by_id")

    def __init__(self, document: RegistryDocument) -> None:
        category_by_id = {category.id: category for category in document.categories}
        self._category_by_id = category_by_id
        self._categories = tuple(
            sorted(
                document.categories,
                key=lambda category: (
                    category.order,
                    category.display_name.casefold(),
                    category.id,
                ),
            )
        )
        self._apps = tuple(
            sorted(
                document.apps,
                key=lambda app: (
                    category_by_id[app.category].order,
                    app.order,
                    app.display_name.casefold(),
                    app.id,
                ),
            )
        )
        self._app_by_id = {app.id: app for app in self._apps}

    @classmethod
    def load_default(cls) -> "AppRegistry":
        return cls.from_source(JsonRegistrySource())

    @classmethod
    def from_source(cls, source: RegistrySource) -> "AppRegistry":
        return cls(source.load())

    @classmethod
    def from_sources(cls, sources: Iterable[RegistrySource]) -> "AppRegistry":
        loaded_sources = tuple(sources)
        if not loaded_sources:
            raise RegistryValidationError(
                [
                    RegistryIssue(
                        code="missing_source",
                        source="registry",
                        message="at least one registry source is required",
                    )
                ]
            )

        documents = tuple(source.load() for source in loaded_sources)
        categories = tuple(
            category
            for document in documents
            for category in document.categories
        )
        apps = tuple(app for document in documents for app in document.apps)
        issues: list[RegistryIssue] = []

        category_ids: dict[str, AppCategory] = {}
        category_orders: dict[int, AppCategory] = {}
        for category in categories:
            if category.id in category_ids:
                issues.append(
                    RegistryIssue(
                        code="duplicate_category_id",
                        source="merged registry",
                        field="categories",
                        message=f"category ID is duplicated: {category.id}",
                    )
                )
            else:
                category_ids[category.id] = category
            if category.order in category_orders:
                issues.append(
                    RegistryIssue(
                        code="duplicate_category_order",
                        source="merged registry",
                        field="categories",
                        message=f"category order is duplicated: {category.order}",
                    )
                )
            else:
                category_orders[category.order] = category

        app_ids: dict[str, AppManifest] = {}
        routes: dict[str, AppManifest] = {}
        orders: dict[tuple[str, int], AppManifest] = {}
        for app in apps:
            if app.id in app_ids:
                issues.append(
                    RegistryIssue(
                        code="duplicate_app_id",
                        source="merged registry",
                        app_id=app.id,
                        field="id",
                        message=f"app ID is duplicated: {app.id}",
                    )
                )
            else:
                app_ids[app.id] = app

            route_key = (
                normalize_internal_route(app.route)
                if app.kind == "internal"
                else app.route
            )
            if route_key in routes:
                issues.append(
                    RegistryIssue(
                        code="duplicate_route",
                        source="merged registry",
                        app_id=app.id,
                        field="route",
                        message=f"route is duplicated: {route_key}",
                    )
                )
            else:
                routes[route_key] = app

            order_key = (app.category, app.order)
            if order_key in orders:
                issues.append(
                    RegistryIssue(
                        code="duplicate_app_order",
                        source="merged registry",
                        app_id=app.id,
                        field="order",
                        message=(
                            f"order {app.order} is duplicated in {app.category}"
                        ),
                    )
                )
            else:
                orders[order_key] = app

            if app.category not in category_ids:
                issues.append(
                    RegistryIssue(
                        code="unknown_category",
                        source="merged registry",
                        app_id=app.id,
                        field="category",
                        message=f"category is not declared: {app.category}",
                    )
                )

        if issues:
            raise RegistryValidationError(issues)

        return cls(
            RegistryDocument(
                schema_version=1,
                categories=categories,
                apps=apps,
            )
        )

    def get_all(self) -> tuple[AppManifest, ...]:
        return self._apps

    def get_enabled(self) -> tuple[AppManifest, ...]:
        return tuple(app for app in self._apps if app.enabled)

    def get_navigation(self) -> tuple[AppManifest, ...]:
        return tuple(
            app
            for app in self._apps
            if app.enabled and app.navigation_visible
        )

    def get_launcher(self) -> tuple[AppManifest, ...]:
        return tuple(
            app
            for app in self._apps
            if app.enabled and app.launcher_visible
        )

    def get_app(self, app_id: str) -> AppManifest | None:
        return self._app_by_id.get(app_id)

    def exists(self, app_id: str) -> bool:
        return app_id in self._app_by_id

    def categories(self) -> tuple[AppCategory, ...]:
        return self._categories

    def search(
        self,
        query: str,
        *,
        include_disabled: bool = False,
    ) -> tuple[AppManifest, ...]:
        normalized_query = " ".join(query.split()).casefold()
        candidates = self._apps if include_disabled else self.get_enabled()
        if not normalized_query:
            return tuple(candidates)

        def matches(app: AppManifest) -> bool:
            fields = (
                app.id,
                app.name,
                app.display_name,
                app.description,
                *app.search_keywords,
            )
            return any(
                normalized_query in " ".join(field.split()).casefold()
                for field in fields
            )

        return tuple(app for app in candidates if matches(app))

    def resolve_route(self, path: str) -> AppManifest | None:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
            return None
        request_path = parsed.path.rstrip("/") or "/"
        matches = []
        for app in self.get_enabled():
            if app.kind != "internal":
                continue
            app_path = app.route.rstrip("/")
            if request_path == app_path or request_path.startswith(f"{app_path}/"):
                matches.append(app)
        if not matches:
            return None
        return max(matches, key=lambda app: len(app.route))

    def get_default_pinned_ids(self) -> tuple[str, ...]:
        return tuple(
            app.id
            for app in self._apps
            if app.enabled and app.default_pinned
        )


# Temporary compatibility surface. Consumers move to AppRegistry in later
# commits and this adapter is removed before portal/registry.py is deleted.
class HubApp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=_SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["internal", "external"]
    url: str
    icon: str
    description: str = Field(max_length=160)
    enabled: bool = True
    navigationVisible: bool = True
    defaultPinned: bool = False
    order: int = 100

    @model_validator(mode="after")
    def validate_urls(self) -> "HubApp":
        if self.kind == "internal" and not self.url.startswith("/apps/"):
            raise ValueError("internal app URL must start with /apps/")
        if self.kind == "external" and not self.url.startswith("https://"):
            raise ValueError("external app URL must use https://")
        if not self.icon.startswith("/static/icons/"):
            raise ValueError("app icons must be local /static/icons/ assets")
        return self


def load_apps(
    registry_path: Path = REGISTRY_PATH,
    *,
    include_disabled: bool = False,
) -> list[HubApp]:
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    if "schema_version" not in payload:
        apps = [HubApp.model_validate(item) for item in payload["apps"]]
        app_ids = [app.id for app in apps]
        if len(app_ids) != len(set(app_ids)):
            raise ValueError("app registry IDs must be unique")
    else:
        document = parse_registry_payload(
            payload,
            source=str(registry_path),
            asset_root=PORTAL_STATIC_DIR,
        )
        apps = [
            HubApp(
                id=app.id,
                name=app.display_name,
                kind=app.kind,
                url=app.route,
                icon=app.icon,
                description=app.description,
                enabled=app.enabled,
                navigationVisible=app.navigation_visible,
                defaultPinned=app.default_pinned,
                order=app.order,
            )
            for app in document.apps
        ]

    visible_apps = apps if include_disabled else [app for app in apps if app.enabled]
    return sorted(visible_apps, key=lambda app: (app.order, app.name.casefold()))


def navigation_apps(apps: list[HubApp]) -> list[HubApp]:
    return [app for app in apps if app.enabled and app.navigationVisible]
