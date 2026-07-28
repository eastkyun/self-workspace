from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import TaskCreate, create_app, initialize_database


@pytest.fixture
def client(tmp_path: Path):
    app = create_app(tmp_path / "test.db")
    with TestClient(app) as test_client:
        yield test_client


def create_task(client: TestClient, title: str, status: str = "todo") -> dict:
    response = client.post(
        "/api/tasks",
        json={"title": title, "description": "", "status": status},
    )
    assert response.status_code == 201
    return response.json()


def tasks_in(client: TestClient, status: str) -> list[dict]:
    response = client.get("/api/tasks")
    assert response.status_code == 200
    return [task for task in response.json() if task["status"] == status]


def test_empty_database_returns_empty_list(client: TestClient):
    response = client.get("/api/tasks")

    assert response.status_code == 200
    assert response.json() == []


def test_create_task_uses_defaults_and_appends_position(client: TestClient):
    first = client.post("/api/tasks", json={"title": "  첫 작업  "})
    second = client.post("/api/tasks", json={"title": "두 번째"})

    assert first.status_code == 201
    assert first.json()["title"] == "첫 작업"
    assert first.json()["status"] == "todo"
    assert first.json()["position"] == 0
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
                [TaskCreate(title=f"작업 {index}") for index in range(30)],
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
    ],
)
def test_create_rejects_invalid_input(client: TestClient, payload: dict):
    response = client.post("/api/tasks", json=payload)

    assert response.status_code == 422


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


def test_serves_page_and_static_assets(client: TestClient):
    page = client.get("/")
    stylesheet = client.get("/static/styles.css")
    script = client.get("/static/app.js")

    assert page.status_code == 200
    assert "Simple Kanban" in page.text
    assert stylesheet.status_code == 200
    assert "--primary" in stylesheet.text
    assert script.status_code == 200
    assert "loadTasks" in script.text
