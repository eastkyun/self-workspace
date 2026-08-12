from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from access.context import PermissionSnapshot, WorkspaceContext
from access.errors import PermissionDenied, WorkspaceNotAvailable
from workspace_platform.search import SearchResult, WorkspaceSearchService


CONTEXT = WorkspaceContext(
    user_id="user-a",
    workspace_id="workspace-a",
    membership_id="member-a",
    role_key="member",
    enabled_app_ids=("notes-app", "calendar-app"),
    authorization_version=7,
)

SNAPSHOT = PermissionSnapshot(
    workspace_id="workspace-a",
    membership_id="member-a",
    role_key="member",
    version=7,
    enabled_apps=(("notes", "notes-app"), ("calendar", "calendar-app")),
    role_permissions=frozenset({"notes.read", "calendar.read"}),
    explicit_allows=frozenset(),
    explicit_denies=frozenset(),
    owner_only_permissions=frozenset(),
)


@dataclass
class Policy:
    enabled: set[str] = field(default_factory=lambda: {"notes", "calendar"})
    permissions: set[str] = field(default_factory=lambda: {"notes.read", "calendar.read"})
    calls: list[tuple[str, str]] = field(default_factory=list)
    snapshot_error: Exception | None = None

    def permission_snapshot(self, context):
        self.calls.append(("snapshot", context.workspace_id))
        if self.snapshot_error:
            raise self.snapshot_error
        return SNAPSHOT

    def authorize_app(self, context, app_key, *, snapshot=None):
        assert context is CONTEXT
        assert snapshot is SNAPSHOT
        self.calls.append(("app", app_key))
        return app_key in self.enabled

    def authorize(self, context, permission_code, resource_owner_id=None, *, snapshot=None):
        assert context is CONTEXT
        assert snapshot is SNAPSHOT
        self.calls.append(("permission", permission_code))
        return permission_code in self.permissions


@dataclass
class Provider:
    key: str
    app_key: str
    required_permissions: tuple[str, ...]
    results: tuple[SearchResult, ...] = ()
    error: Exception | None = None
    calls: list[tuple[WorkspaceContext, str, int, int]] = field(default_factory=list)

    def search(self, context, query, limit, offset):
        self.calls.append((context, query, limit, offset))
        if self.error:
            raise self.error
        return self.results


def result(resource_id: str, title: str, score: float, workspace="workspace-a"):
    return SearchResult(workspace, resource_id, title, f"/{resource_id}", score)


def test_search_passes_exact_context_and_returns_deterministic_groups_and_rank():
    notes = Provider(
        "notes-main", "notes", ("notes.read",),
        (result("3", "Zulu", 1), result("2", "alpha", 4), result("1", "Alpha", 4)),
    )
    calendar = Provider("calendar-main", "calendar", ("calendar.read",), (result("9", "Event", 2),))
    service = WorkspaceSearchService(Policy(), (notes, calendar))

    response = service.search(CONTEXT, "  roadmap  ", limit=2, offset=3)

    assert response.query == "roadmap"
    assert [group.provider_key for group in response.groups] == ["calendar-main", "notes-main"]
    assert [item.resource_id for item in response.groups[1].results] == ["1", "2"]
    assert notes.calls == [(CONTEXT, "roadmap", 2, 3)]
    assert calendar.calls == [(CONTEXT, "roadmap", 2, 3)]
    assert response.failures == ()


def test_disabled_and_unauthorized_providers_are_never_called():
    policy = Policy(enabled={"notes"}, permissions=set())
    notes = Provider("notes", "notes", ("notes.read",))
    calendar = Provider("calendar", "calendar", ("calendar.read",))

    response = WorkspaceSearchService(policy, (notes, calendar)).search(CONTEXT, "x")

    assert response.groups == ()
    assert response.failures == ()
    assert notes.calls == []
    assert calendar.calls == []


def test_ordinary_failure_is_isolated_and_does_not_hide_healthy_provider():
    broken = Provider("notes", "notes", ("notes.read",), error=RuntimeError("database busy"))
    healthy = Provider("calendar", "calendar", ("calendar.read",), (result("1", "Meeting", 1),))

    response = WorkspaceSearchService(Policy(), (broken, healthy)).search(CONTEXT, "meet")

    assert [group.provider_key for group in response.groups] == ["calendar"]
    assert [(failure.provider_key, failure.code) for failure in response.failures] == [
        ("notes", "provider_unavailable")
    ]


def test_authorization_failure_from_policy_or_provider_fails_closed():
    policy = Policy(snapshot_error=WorkspaceNotAvailable("revoked"))
    provider = Provider("notes", "notes", ("notes.read",))
    with pytest.raises(WorkspaceNotAvailable):
        WorkspaceSearchService(policy, (provider,)).search(CONTEXT, "secret")
    assert provider.calls == []

    provider.error = PermissionDenied("resource denied")
    with pytest.raises(PermissionDenied):
        WorkspaceSearchService(Policy(), (provider,)).search(CONTEXT, "secret")


def test_cross_workspace_provider_result_is_suppressed_as_provider_failure():
    provider = Provider(
        "notes", "notes", ("notes.read",),
        (result("foreign", "Must not leak", 100, workspace="workspace-b"),),
    )

    response = WorkspaceSearchService(Policy(), (provider,)).search(CONTEXT, "secret")

    assert response.groups == ()
    assert response.failures[0].provider_key == "notes"


def test_blank_query_does_not_resolve_authorization_or_call_providers():
    policy = Policy()
    provider = Provider("notes", "notes", ("notes.read",))

    response = WorkspaceSearchService(policy, (provider,)).search(CONTEXT, "   ")

    assert response.query == ""
    assert policy.calls == []
    assert provider.calls == []


def test_default_page_size_is_five_results_per_provider():
    provider = Provider(
        "notes", "notes", ("notes.read",),
        tuple(result(str(index), f"Note {index}", 10 - index) for index in range(7)),
    )

    response = WorkspaceSearchService(Policy(), (provider,)).search(CONTEXT, "note")

    assert provider.calls == [(CONTEXT, "note", 5, 0)]
    assert len(response.groups[0].results) == 5


@pytest.mark.parametrize("limit,offset", [(0, 0), (-1, 0), (1, -1)])
def test_invalid_pagination_is_rejected(limit, offset):
    with pytest.raises(ValueError):
        WorkspaceSearchService(Policy(), ()).search(CONTEXT, "x", limit=limit, offset=offset)


def test_duplicate_provider_keys_are_rejected():
    with pytest.raises(ValueError, match="unique"):
        WorkspaceSearchService(
            Policy(),
            (Provider("same", "notes", ()), Provider("same", "calendar", ())),
        )
