"""Public recipe page tests."""

import json
import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import db
import recipes
import service


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
    app.include_router(recipes.router)
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


def _create_recipe(
    conn: sqlite3.Connection,
    server_id: int,
    *,
    slug: str,
    title: str,
    published: bool,
) -> dict[str, Any]:
    return service.create_recipe(
        conn,
        slug=slug,
        title=title,
        summary=f"{title} summary.",
        department="Operations",
        skill="intermediate",
        agent_instructions=f"Use {title} carefully.",
        example_prompts=[f"Run {title}"],
        destinations=["Copilot Studio"],
        published=published,
        tools=[{"server_id": server_id, "tool_name": "list_orders"}],
    )


def test_draft_recipe_detail_returns_404(client: TestClient, conn: sqlite3.Connection) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    _create_recipe(
        conn,
        int(server["id"]),
        slug="draft-triage",
        title="Draft triage",
        published=False,
    )

    response = client.get("/r/draft-triage")

    assert response.status_code == 404


def test_unknown_recipe_detail_returns_404(client: TestClient) -> None:
    response = client.get("/r/missing-recipe")

    assert response.status_code == 404


def test_published_recipe_detail_returns_200(client: TestClient, conn: sqlite3.Connection) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    _create_recipe(
        conn,
        int(server["id"]),
        slug="published-triage",
        title="Published triage",
        published=True,
    )

    response = client.get("/r/published-triage")

    assert response.status_code == 200, response.text
    assert "Published triage" in response.text


def test_index_lists_only_published_recipes(client: TestClient, conn: sqlite3.Connection) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    _create_recipe(
        conn,
        int(server["id"]),
        slug="published-triage",
        title="Published triage",
        published=True,
    )
    _create_recipe(
        conn,
        int(server["id"]),
        slug="draft-triage",
        title="Draft triage",
        published=False,
    )

    response = client.get("/r/")

    assert response.status_code == 200, response.text
    assert "Published triage" in response.text
    assert "Draft triage" not in response.text


def test_public_json_lists_only_published_recipes(
    client: TestClient,
    conn: sqlite3.Connection,
) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    _create_recipe(
        conn,
        int(server["id"]),
        slug="published-triage",
        title="Published triage",
        published=True,
    )
    _create_recipe(
        conn,
        int(server["id"]),
        slug="draft-triage",
        title="Draft triage",
        published=False,
    )

    response = client.get("/api/public/recipes")

    assert response.status_code == 200, response.text
    titles = [recipe["title"] for recipe in response.json()["recipes"]]
    assert titles == ["Published triage"]


def test_public_json_omits_sensitive_keys(client: TestClient, conn: sqlite3.Connection) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    _create_recipe(
        conn,
        int(server["id"]),
        slug="published-triage",
        title="Published triage",
        published=True,
    )

    response = client.get("/api/public/recipes")
    serialized = json.dumps(response.json()).lower()

    assert response.status_code == 200, response.text
    for forbidden in ("api_key", "key_hash", "password_hash", "token", "call_log"):
        assert forbidden not in serialized


def test_public_routes_work_without_auth_headers(
    client: TestClient,
    conn: sqlite3.Connection,
) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    _create_recipe(
        conn,
        int(server["id"]),
        slug="published-triage",
        title="Published triage",
        published=True,
    )

    response = client.get("/r/published-triage")

    assert response.status_code == 200, response.text
    assert "authorization" not in response.request.headers
    assert "x-api-key" not in response.request.headers
    assert "cookie" not in response.request.headers


def test_recipe_detail_escapes_xss_title(client: TestClient, conn: sqlite3.Connection) -> None:
    server = _make_server_with_endpoint(conn, "orders")
    _create_recipe(
        conn,
        int(server["id"]),
        slug="xss-title",
        title="<script>alert(1)</script>",
        published=True,
    )

    response = client.get("/r/xss-title")

    assert response.status_code == 200, response.text
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text


def test_public_routes_have_no_write_methods(client: TestClient) -> None:
    for method in (client.post, client.put, client.patch, client.delete):
        assert method("/r/").status_code == 405
        assert method("/api/public/recipes").status_code == 405
