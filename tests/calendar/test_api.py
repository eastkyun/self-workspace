from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from apps.calendar.app.main import TaskCreate, create_app, initialize_database

START_AT = "2026-07-28T09:00:00+09:00"
DUE_AT = "2026-07-28T10:00:00+09:00"


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(tmp_path / "test.db")
    with TestClient(app) as test_client:
        yield test_client


def create_task(client: TestClient, title: str, status: str = "todo") -> dict:
    response = client.post(
        "/api/tasks",
        json={
            "title": title,
            "description": "",
            "status": status,
            "start_at": START_AT,
            "due_at": DUE_AT,
        },
    )
    assert response.status_code == 201
    return response.json()


def tasks_in(client: TestClient, status: str) -> list[dict]:
    response = client.get("/api/tasks")
    assert response.status_code == 200
    return [task for task in response.json() if task["status"] == status]


def create_topic(
    client: TestClient, name: str, color: str = "#3366CC"
) -> dict:
    response = client.post("/api/topics", json={"name": name, "color": color})
    assert response.status_code == 201
    return response.json()


def test_empty_database_returns_empty_list(client: TestClient):
    response = client.get("/api/tasks")

    assert response.status_code == 200
    assert response.json() == []


def test_create_task_uses_defaults_and_appends_position(client: TestClient):
    first = client.post(
        "/api/tasks",
        json={"title": "  첫 작업  ", "start_at": START_AT, "due_at": DUE_AT},
    )
    second = client.post(
        "/api/tasks",
        json={"title": "두 번째", "start_at": START_AT, "due_at": DUE_AT},
    )

    assert first.status_code == 201
    assert first.json()["title"] == "첫 작업"
    assert first.json()["status"] == "todo"
    assert first.json()["position"] == 0
    assert first.json()["all_day"] is False
    assert first.json()["start_at"] == START_AT
    assert first.json()["due_at"] == DUE_AT
    assert second.json()["position"] == 1


def test_concurrent_creates_assign_unique_contiguous_positions(tmp_path: Path):
    database_path = tmp_path / "concurrent.db"
    initialize_database(database_path)
    app = create_app(database_path)
    create_task_endpoint = next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == "/api/tasks"
        and "POST" in getattr(route, "methods", set())
    )

    with ThreadPoolExecutor(max_workers=10) as executor:
        tasks = list(
            executor.map(
                create_task_endpoint,
                [
                    TaskCreate(
                        title=f"작업 {index}",
                        start_at=START_AT,
                        due_at=DUE_AT,
                    )
                    for index in range(30)
                ],
            )
        )

    assert sorted(task.position for task in tasks) == list(range(30))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 121},
        {"title": "작업", "status": "blocked"},
        {"title": "작업", "start_at": START_AT},
        {"title": "작업", "start_at": START_AT, "due_at": "2026-07-28T10:00:00"},
        {
            "title": "작업",
            "start_at": DUE_AT,
            "due_at": START_AT,
        },
    ],
)
def test_create_rejects_invalid_input(client: TestClient, payload: dict):
    response = client.post("/api/tasks", json=payload)

    assert response.status_code == 422


def test_create_normalizes_offsets_to_kst(client: TestClient):
    response = client.post(
        "/api/tasks",
        json={
            "title": "UTC 일정",
            "start_at": "2026-07-28T00:00:00Z",
            "due_at": "2026-07-28T01:00:00Z",
        },
    )

    assert response.status_code == 201
    assert response.json()["start_at"] == START_AT
    assert response.json()["due_at"] == DUE_AT


def test_create_all_day_normalizes_to_kst_day_boundaries(client: TestClient):
    response = client.post(
        "/api/tasks",
        json={
            "title": "종일 일정",
            "all_day": True,
            "start_at": "2026-07-28T15:30:00+09:00",
            "due_at": "2026-07-30T08:30:00+09:00",
        },
    )

    assert response.status_code == 201
    task = response.json()
    assert task["all_day"] is True
    assert task["start_at"] == "2026-07-28T00:00:00+09:00"
    assert task["due_at"] == "2026-07-30T23:59:59+09:00"


def test_partially_updates_title_and_description(client: TestClient):
    task = create_task(client, "초기 제목")

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"title": "  변경 제목 ", "description": "설명"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "변경 제목"
    assert response.json()["description"] == "설명"
    assert response.json()["status"] == "todo"


def test_patch_schedule_merges_with_stored_values_and_validates(client: TestClient):
    task = create_task(client, "일정")

    invalid = client.patch(
        f"/api/tasks/{task['id']}",
        json={"start_at": "2026-07-28T11:00:00+09:00"},
    )
    valid = client.patch(
        f"/api/tasks/{task['id']}",
        json={"due_at": "2026-07-28T11:00:00+09:00"},
    )

    assert invalid.status_code == 422
    assert valid.status_code == 200
    assert valid.json()["start_at"] == START_AT
    assert valid.json()["due_at"] == "2026-07-28T11:00:00+09:00"


def test_patch_all_day_normalizes_existing_schedule(client: TestClient):
    task = create_task(client, "일정")

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"all_day": True},
    )

    assert response.status_code == 200
    assert response.json()["start_at"] == "2026-07-28T00:00:00+09:00"
    assert response.json()["due_at"] == "2026-07-28T23:59:59+09:00"


def test_reorders_tasks_within_a_column(client: TestClient):
    first = create_task(client, "첫 번째")
    create_task(client, "두 번째")
    create_task(client, "세 번째")

    response = client.patch(f"/api/tasks/{first['id']}", json={"position": 2})

    assert response.status_code == 200
    ordered = tasks_in(client, "todo")
    assert [task["title"] for task in ordered] == ["두 번째", "세 번째", "첫 번째"]
    assert [task["position"] for task in ordered] == [0, 1, 2]


def test_moves_task_between_columns_and_compacts_both(client: TestClient):
    first = create_task(client, "이동할 작업")
    create_task(client, "남을 작업")
    create_task(client, "진행 A", "doing")
    create_task(client, "진행 B", "doing")

    response = client.patch(
        f"/api/tasks/{first['id']}",
        json={"status": "doing", "position": 1},
    )

    assert response.status_code == 200
    assert [task["position"] for task in tasks_in(client, "todo")] == [0]
    doing = tasks_in(client, "doing")
    assert [task["title"] for task in doing] == ["진행 A", "이동할 작업", "진행 B"]
    assert [task["position"] for task in doing] == [0, 1, 2]


def test_delete_compacts_remaining_positions(client: TestClient):
    create_task(client, "첫 번째")
    second = create_task(client, "두 번째")
    create_task(client, "세 번째")

    response = client.delete(f"/api/tasks/{second['id']}")

    assert response.status_code == 204
    remaining = tasks_in(client, "todo")
    assert [task["title"] for task in remaining] == ["첫 번째", "세 번째"]
    assert [task["position"] for task in remaining] == [0, 1]


def test_missing_task_returns_not_found(client: TestClient):
    assert client.patch("/api/tasks/999", json={"title": "없음"}).status_code == 404
    assert client.delete("/api/tasks/999").status_code == 404


def test_rejects_null_update_fields(client: TestClient):
    task = create_task(client, "작업")

    response = client.patch(f"/api/tasks/{task['id']}", json={"status": None})

    assert response.status_code == 422


def test_lists_tasks_in_calendar_order(client: TestClient):
    later = client.post(
        "/api/tasks",
        json={
            "title": "나중",
            "start_at": "2026-07-29T09:00:00+09:00",
            "due_at": "2026-07-29T10:00:00+09:00",
        },
    ).json()
    earlier = client.post(
        "/api/tasks",
        json={
            "title": "먼저",
            "status": "done",
            "start_at": START_AT,
            "due_at": DUE_AT,
        },
    ).json()

    response = client.get("/api/tasks")

    assert [task["id"] for task in response.json()] == [earlier["id"], later["id"]]


def test_topic_crud_allows_duplicate_colors_and_sorts_names(client: TestClient):
    beta = create_topic(client, "beta", "#3366cc")
    alpha = create_topic(client, "Alpha", "#3366CC")

    response = client.get("/api/topics")

    assert [topic["id"] for topic in response.json()] == [alpha["id"], beta["id"]]
    assert {topic["color"] for topic in response.json()} == {"#3366CC"}

    updated = client.patch(
        f"/api/topics/{beta['id']}",
        json={"name": "Gamma", "color": "#AA44CC"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Gamma"
    assert updated.json()["color"] == "#AA44CC"

    deleted = client.delete(f"/api/topics/{alpha['id']}")
    assert deleted.status_code == 204
    assert [topic["id"] for topic in client.get("/api/topics").json()] == [
        beta["id"]
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"name": "", "color": "#3366CC"},
        {"name": "x" * 41, "color": "#3366CC"},
        {"name": "업무", "color": "blue"},
        {"name": "업무", "color": "#12345"},
    ],
)
def test_topic_create_rejects_invalid_input(client: TestClient, payload: dict):
    assert client.post("/api/topics", json=payload).status_code == 422


def test_topic_names_are_unique_case_insensitively(client: TestClient):
    topic = create_topic(client, "Work")

    duplicate_create = client.post(
        "/api/topics", json={"name": " work ", "color": "#CC6633"}
    )
    other = create_topic(client, "개인", "#CC6633")
    duplicate_update = client.patch(
        f"/api/topics/{other['id']}", json={"name": "WORK"}
    )

    assert duplicate_create.status_code == 409
    assert duplicate_update.status_code == 409
    assert client.get("/api/topics").json()[0]["id"] == topic["id"]


def test_topic_update_requires_a_non_null_field_and_missing_topic_is_404(
    client: TestClient,
):
    topic = create_topic(client, "업무")

    assert client.patch(f"/api/topics/{topic['id']}", json={}).status_code == 422
    assert (
        client.patch(
            f"/api/topics/{topic['id']}", json={"color": None}
        ).status_code
        == 422
    )
    assert client.patch("/api/topics/999", json={"name": "없음"}).status_code == 404
    assert client.delete("/api/topics/999").status_code == 404


def test_task_supports_multiple_topics_and_patch_semantics(client: TestClient):
    work = create_topic(client, "Work", "#3366CC")
    personal = create_topic(client, "personal", "#CC6633")

    created = client.post(
        "/api/tasks",
        json={
            "title": "다중 주제",
            "start_at": START_AT,
            "due_at": DUE_AT,
            "topic_ids": [personal["id"], work["id"], personal["id"]],
        },
    )

    assert created.status_code == 201
    task = created.json()
    assert [topic["id"] for topic in task["topics"]] == [
        personal["id"],
        work["id"],
    ]

    kept = client.patch(f"/api/tasks/{task['id']}", json={"title": "수정"})
    assert [topic["id"] for topic in kept.json()["topics"]] == [
        personal["id"],
        work["id"],
    ]

    cleared = client.patch(f"/api/tasks/{task['id']}", json={"topic_ids": []})
    assert cleared.status_code == 200
    assert cleared.json()["topics"] == []


def test_unknown_topic_ids_reject_task_changes_atomically(client: TestClient):
    topic = create_topic(client, "업무")
    task = client.post(
        "/api/tasks",
        json={
            "title": "원본",
            "start_at": START_AT,
            "due_at": DUE_AT,
            "topic_ids": [topic["id"]],
        },
    ).json()

    response = client.patch(
        f"/api/tasks/{task['id']}",
        json={"title": "변경되면 안 됨", "topic_ids": [999]},
    )

    assert response.status_code == 422
    stored = client.get("/api/tasks").json()[0]
    assert stored["title"] == "원본"
    assert [item["id"] for item in stored["topics"]] == [topic["id"]]


def test_deleting_topic_removes_only_relations_and_unclassifies_task(
    client: TestClient,
):
    topic = create_topic(client, "업무")
    task = client.post(
        "/api/tasks",
        json={
            "title": "유지할 일정",
            "start_at": START_AT,
            "due_at": DUE_AT,
            "topic_ids": [topic["id"]],
        },
    ).json()

    assert client.delete(f"/api/topics/{topic['id']}").status_code == 204
    stored = client.get("/api/tasks").json()
    assert [item["id"] for item in stored] == [task["id"]]
    assert stored[0]["topics"] == []


def test_deleting_task_cascades_relations_but_keeps_topic(client: TestClient):
    topic = create_topic(client, "업무")
    task = client.post(
        "/api/tasks",
        json={
            "title": "삭제할 일정",
            "start_at": START_AT,
            "due_at": DUE_AT,
            "topic_ids": [topic["id"]],
        },
    ).json()

    assert client.delete(f"/api/tasks/{task['id']}").status_code == 204
    with sqlite3.connect(client.app.state.database_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM task_topics"
        ).fetchone()[0] == 0
    assert [item["id"] for item in client.get("/api/topics").json()] == [
        topic["id"]
    ]


def test_serves_page_and_static_assets(client: TestClient):
    page = client.get("/")
    calendar_icon = client.get("/static/icons/calendar.svg")
    shell_stylesheet = client.get("/static/shared/shell.css")
    shell_script = client.get("/static/shared/shell.js")
    stylesheet = client.get("/static/styles.css")
    script = client.get("/static/app.js")
    adapter = client.get("/static/calendar-adapter.js")
    fullcalendar = client.get(
        "/static/vendor/fullcalendar/7.0.2/fullcalendar.global.js"
    )
    locales = client.get(
        "/static/vendor/fullcalendar/7.0.2/locales-all/global.js"
    )
    skeleton = client.get("/static/vendor/fullcalendar/7.0.2/skeleton.css")
    classic_script = client.get(
        "/static/vendor/fullcalendar/7.0.2/themes/classic/global.js"
    )
    classic_theme = client.get(
        "/static/vendor/fullcalendar/7.0.2/themes/classic/theme.css"
    )
    classic_palette = client.get(
        "/static/vendor/fullcalendar/7.0.2/themes/classic/palette.css"
    )
    license_file = client.get("/static/vendor/fullcalendar/7.0.2/LICENSE.md")
    pretendard = client.get(
        "/static/vendor/pretendard/1.3.9/PretendardVariable.woff2"
    )
    pretendard_license = client.get(
        "/static/vendor/pretendard/1.3.9/LICENSE.txt"
    )

    assert page.status_code == 200
    assert calendar_icon.status_code == 200
    assert 'id="sidebar"' in page.text
    assert 'class="workspace-header"' in page.text
    assert page.text.count('aria-current="page"') == 1
    assert 'id="calendar"' in page.text
    assert 'id="create-menu-button"' in page.text
    assert 'id="topic-filter-list"' in page.text
    assert 'id="topic-overflow-button"' in page.text
    assert 'id="topic-overflow-popover"' in page.text
    assert 'id="selected-pagination"' in page.text
    assert 'id="topic-dialog"' in page.text
    assert 'id="task-topic-options"' in page.text
    assert "cdn.jsdelivr" not in page.text
    assert "unpkg" not in page.text
    assert shell_stylesheet.status_code == 200
    assert shell_script.status_code == 200
    assert stylesheet.status_code == 200
    assert ".calendar-shell" in stylesheet.text
    assert script.status_code == 200
    assert "loadTasks" in script.text
    assert adapter.status_code == 200
    assert "taskToCalendarEvent" in adapter.text
    assert fullcalendar.status_code == 200
    assert "FullCalendar (Vanilla JS) v7.0.2" in fullcalendar.text
    assert locales.status_code == 200
    assert skeleton.status_code == 200
    assert classic_script.status_code == 200
    assert classic_theme.status_code == 200
    assert classic_palette.status_code == 200
    assert license_file.status_code == 200
    assert "MIT License" in license_file.text
    assert pretendard.status_code == 200
    assert pretendard.headers["content-type"] == "font/woff2"
    assert pretendard_license.status_code == 200
    assert "SIL OPEN FONT LICENSE" in pretendard_license.text
