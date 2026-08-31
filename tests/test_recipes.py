"""Tests for recipe metadata and cross-server tool references."""

import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api
import auth
import db
import service
import store


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.init_db(":memory:")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def client(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("AUTH_DISABLED", "1")
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[auth.get_conn] = lambda: conn
    with TestClient(app) as test_client:
        yield test_client


def _make_server_with_endpoint(
    conn: sqlite3.Connection,
    slug: str,
    tool_name: str = "list_orders",
    path: str = "/orders",
) -> dict[str, Any]:
    server = service.create_server(conn, slug, slug.title(), "", "none")
    dataset = service.create_dataset(conn, int(server["id"]), "orders", "id", [{"id": 1}])
    service.create_endpoint(conn, int(server["id"]), path, "GET", tool_name, "", int(dataset["id"]))
    return server


def _recipe_payload(server_id: int, tool_name: str = "list_orders") -> dict[str, Any]:
    return {
        "slug": "triage-orders",
        "title": "Triage orders",
        "summary": "Find stuck orders.",
        "department": "Operations",
        "skill": "intermediate",
        "agent_instructions": "You are an order triage agent.",
        "example_prompts": ["Find open orders"],
        "destinations": ["Copilot Studio"],
        "published": True,
        "tools": [{"server_id": server_id, "tool_name": tool_name}],
    }


def test_create_read_back_includes_normalised_recipe_fields(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    server = _make_server_with_endpoint(conn, "orders")

    created = client.post("/api/recipes", json=_recipe_payload(int(server["id"])))
    assert created.status_code == 201, created.text
    recipe_id = created.json()["id"]

    fetched = client.get(f"/api/recipes/{recipe_id}")
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["tools"][0]["tool_name"] == "list_orders"
    assert body["published"] is True
    assert body["example_prompts"] == ["Find open orders"]


def test_duplicate_slug_returns_409(client: TestClient, conn: sqlite3.Connection) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    payload = _recipe_payload(int(server["id"]))
    assert client.post("/api/recipes", json=payload).status_code == 201

    duplicate = client.post("/api/recipes", json=payload)
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]


def test_invalid_slug_format_returns_400(client: TestClient, conn: sqlite3.Connection) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    payload = {**_recipe_payload(int(server["id"])), "slug": "Bad Slug"}

    response = client.post("/api/recipes", json=payload)
    assert response.status_code == 400
    assert "invalid slug" in response.json()["detail"]


def test_invalid_skill_returns_422(client: TestClient, conn: sqlite3.Connection) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    payload = {**_recipe_payload(int(server["id"])), "skill": "expert"}

    response = client.post("/api/recipes", json=payload)
    assert response.status_code == 422


def test_tool_ref_to_missing_server_returns_400(client: TestClient) -> None:
    response = client.post("/api/recipes", json=_recipe_payload(9999))

    assert response.status_code == 400
    assert "server 9999" in response.json()["detail"]


def test_tool_ref_to_missing_tool_returns_400(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    payload = _recipe_payload(int(server["id"]), "missing_tool")

    response = client.post("/api/recipes", json=payload)
    assert response.status_code == 400
    assert "missing_tool" in response.json()["detail"]


def test_cross_server_recipe_returns_both_server_slugs(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    orders = _make_server_with_endpoint(conn, "orders", "list_orders")
    hris = _make_server_with_endpoint(conn, "hris", "list_employees", "/employees")
    payload = {
        **_recipe_payload(int(orders["id"])),
        "tools": [
            {"server_id": int(orders["id"]), "tool_name": "list_orders"},
            {"server_id": int(hris["id"]), "tool_name": "list_employees"},
        ],
    }

    created = client.post("/api/recipes", json=payload)
    assert created.status_code == 201, created.text
    server_slugs = {tool["server_slug"] for tool in created.json()["tools"]}
    assert server_slugs == {"orders", "hris"}


def test_put_tools_replaces_set_and_reassigns_ordinals(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    orders = _make_server_with_endpoint(conn, "orders", "list_orders")
    hris = _make_server_with_endpoint(conn, "hris", "list_employees", "/employees")
    recipe = client.post("/api/recipes", json=_recipe_payload(int(orders["id"]))).json()

    response = client.put(
        f"/api/recipes/{recipe['id']}/tools",
        json={
            "tools": [
                {"server_id": int(hris["id"]), "tool_name": "list_employees"},
                {"server_id": int(orders["id"]), "tool_name": "list_orders"},
            ]
        },
    )
    assert response.status_code == 200, response.text
    tools = response.json()["tools"]
    assert [tool["server_slug"] for tool in tools] == ["hris", "orders"]
    assert [tool["ordinal"] for tool in tools] == [0, 1]


def test_delete_recipe_cascades_to_recipe_tool_rows(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    recipe = client.post("/api/recipes", json=_recipe_payload(int(server["id"]))).json()

    deleted = client.delete(f"/api/recipes/{recipe['id']}")
    assert deleted.status_code == 204, deleted.text
    assert store.list_recipe_tools(conn, int(recipe["id"])) == []


def test_deleted_endpoint_leaves_dangling_ref_reported_by_validation(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    recipe = service.create_recipe(conn, **_recipe_payload(int(server["id"])))
    endpoint = service.list_endpoints(conn, int(server["id"]))[0]

    service.delete_endpoint(conn, int(endpoint["id"]))
    report = service.validate_recipes(conn)
    assert report["ok"] is False
    assert report["recipe_count"] == 1
    assert report["issues"][0]["recipe_id"] == recipe["id"]
    assert "no longer exists" in report["issues"][0]["message"]

    api_report = client.get("/api/recipes/validate")
    assert api_report.status_code == 200, api_report.text
    assert api_report.json()["issues"][0]["recipe_id"] == recipe["id"]


def test_validate_route_is_not_captured_by_recipe_id(client: TestClient) -> None:
    response = client.get("/api/recipes/validate")

    assert response.status_code == 200, response.text
    assert set(response.json()) == {"recipe_count", "ok", "issues"}
