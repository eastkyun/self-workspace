from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.notes.app.main import create_app


@pytest.fixture
def client(tmp_path: Path):
    with TestClient(create_app(tmp_path / "notes.db")) as active:
        yield active


def create_note(client: TestClient, title: str = "Note") -> dict:
    response = client.post(
        "/api/notes",
        json={
            "title": title,
            "content": "",
            "note_type": "general",
            "is_pinned": False,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_crud_contract(client: TestClient) -> None:
    note = create_note(client, " First ")
    assert note["title"] == "First"
    assert client.get("/api/notes").json()[0]["id"] == note["id"]
    assert client.get(f"/api/notes/{note['id']}").status_code == 200

    updated = client.put(
        f"/api/notes/{note['id']}",
        json={
            "title": "Updated",
            "content": "Body",
            "note_type": "meeting_minutes",
            "is_pinned": True,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["is_pinned"] is True
    assert client.delete(f"/api/notes/{note['id']}").status_code == 204
    assert client.get(f"/api/notes/{note['id']}").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": ""},
        {"title": " "},
        {"title": "x" * 121},
        {"title": "Note", "content": "x" * 100001},
        {"title": "Note", "note_type": "invalid"},
        {"title": "Note", "unknown": True},
    ],
)
def test_create_validation(client: TestClient, payload: dict) -> None:
    assert client.post("/api/notes", json=payload).status_code == 422


def test_list_search_filter_pagination_and_invalid_query(
    client: TestClient,
) -> None:
    create_note(client, "Alpha")
    create_note(client, "Beta")

    assert [item["title"] for item in client.get("/api/notes?q=Alpha").json()] == [
        "Alpha"
    ]
    assert len(client.get("/api/notes?limit=1&offset=1").json()) == 1
    assert client.get("/api/notes?limit=0").status_code == 422
    assert client.get("/api/notes?source_app=calendar").status_code == 422


def test_not_found_and_malformed_ids(client: TestClient) -> None:
    assert client.get("/api/notes/999").status_code == 404
    assert client.put(
        "/api/notes/999",
        json={"title": "Missing", "content": ""},
    ).status_code == 404
    assert client.delete("/api/notes/999").status_code == 404
    assert client.get("/api/notes/not-an-int").status_code == 422
