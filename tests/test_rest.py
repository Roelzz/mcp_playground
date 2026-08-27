import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import db
import rest
import store


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.init_db(":memory:")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def client(conn: sqlite3.Connection) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(rest.router)
    app.dependency_overrides[auth.get_conn] = lambda: conn
    with TestClient(app) as test_client:
        yield test_client


def _create_playground(
    conn: sqlite3.Connection,
    slug: str = "zava-test",
    auth_mode: str = "none",
) -> dict[str, Any]:
    server_id = store.create_server(conn, slug, slug.title(), "", auth_mode)
    dataset_id = store.create_dataset(conn, server_id, "orders", "id")
    rows = [
        {"id": 1, "status": "open", "name": "Alpha Widget", "customer": "Acme", "priority": 1},
        {"id": 2, "status": "closed", "name": "Beta Gear", "customer": "Beta", "priority": 2},
        {"id": 42, "status": "open", "name": "Widget Deluxe", "customer": "Zava", "priority": 1},
    ]
    store.add_rows(conn, dataset_id, rows)
    endpoints = {
        "list": _create_endpoint(conn, server_id, dataset_id, "GET", "/orders", "list_orders"),
        "search": _create_endpoint(
            conn, server_id, dataset_id, "GET", "/orders/search", "search_orders"
        ),
        "get": _create_endpoint(conn, server_id, dataset_id, "GET", "/orders/{id}", "get_order"),
        "create": _create_endpoint(conn, server_id, dataset_id, "POST", "/orders", "create_order"),
        "put": _create_endpoint(conn, server_id, dataset_id, "PUT", "/orders/{id}", "put_order"),
        "patch": _create_endpoint(
            conn, server_id, dataset_id, "PATCH", "/orders/{id}", "patch_order"
        ),
        "delete": _create_endpoint(
            conn, server_id, dataset_id, "DELETE", "/orders/{id}", "delete_order"
        ),
    }
    return {"server_id": server_id, "dataset_id": dataset_id, "rows": rows, "endpoints": endpoints}


def _create_endpoint(
    conn: sqlite3.Connection,
    server_id: int,
    dataset_id: int,
    method: str,
    path: str,
    tool_name: str,
) -> dict[str, Any]:
    endpoint_id = store.create_endpoint(
        conn, server_id, path, method, tool_name, "", dataset_id, []
    )
    endpoint = store.get_endpoint(conn, endpoint_id)
    assert endpoint is not None
    return endpoint


def _create_api_key(conn: sqlite3.Connection, label: str = "rest-key") -> str:
    key = auth.generate_api_key()
    store.create_api_key(conn, label, auth.hash_api_key(key), "readonly")
    return key


def test_get_orders_returns_array(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_playground(conn)

    response = client.get("/mock/zava-test/orders")

    assert response.status_code == 200, response.text
    assert response.json() == [
        {"id": 1, "status": "open", "name": "Alpha Widget", "customer": "Acme", "priority": 1},
        {"id": 2, "status": "closed", "name": "Beta Gear", "customer": "Beta", "priority": 2},
        {"id": 42, "status": "open", "name": "Widget Deluxe", "customer": "Zava", "priority": 1},
    ]


def test_query_filter_narrows_results(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_playground(conn)

    response = client.get("/mock/zava-test/orders?status=open")

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [1, 42]


def test_limit_query_param_is_honoured(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_playground(conn)

    response = client.get("/mock/zava-test/orders?limit=1")

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [1]


def test_search_literal_route_wins_over_id_placeholder(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    _create_playground(conn)

    response = client.get("/mock/zava-test/orders/search?q=widget")

    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [1, 42]
    traffic = store.get_traffic(conn)
    assert traffic[0]["tool_name"] == "search_orders"
    assert traffic[0]["request"] == {"q": "widget"}


def test_get_order_by_id_returns_object(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_playground(conn)

    response = client.get("/mock/zava-test/orders/42")

    assert response.status_code == 200, response.text
    assert response.json()["id"] == 42
    assert response.json()["name"] == "Widget Deluxe"


def test_get_nonexistent_id_returns_404(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_playground(conn)

    response = client.get("/mock/zava-test/orders/999")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_post_order_returns_201_created_row(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_playground(conn)

    response = client.post(
        "/mock/zava-test/orders",
        json={"status": "open", "name": "New Widget", "customer": "Fresh", "priority": 3},
    )

    assert response.status_code == 201, response.text
    assert response.json() == {
        "status": "open",
        "name": "New Widget",
        "customer": "Fresh",
        "priority": 3,
        "id": 43,
    }


def test_post_unknown_field_returns_400(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_playground(conn)

    response = client.post(
        "/mock/zava-test/orders",
        json={"status": "open", "name": "New Widget", "unknown": "nope"},
    )

    assert response.status_code == 400
    assert "unknown" in response.json()["detail"]


def test_put_and_patch_update_order(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_playground(conn)

    put_response = client.put(
        "/mock/zava-test/orders/42",
        json={"status": "closed", "name": "Updated Widget"},
    )
    patch_response = client.patch("/mock/zava-test/orders/42", json={"status": "open"})

    assert put_response.status_code == 200, put_response.text
    assert put_response.json()["status"] == "closed"
    assert put_response.json()["name"] == "Updated Widget"
    assert patch_response.status_code == 200, patch_response.text
    assert patch_response.json()["status"] == "open"
    assert patch_response.json()["name"] == "Updated Widget"


def test_delete_order_returns_deleted_payload(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_playground(conn)

    response = client.delete("/mock/zava-test/orders/42")

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "id": 42}


def test_unknown_server_slug_returns_404(client: TestClient) -> None:
    response = client.get("/mock/missing/orders")

    assert response.status_code == 404
    assert response.json()["detail"] == "server 'missing' not found"


def test_unknown_path_on_known_server_returns_404(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    _create_playground(conn)

    response = client.get("/mock/zava-test/customers")

    assert response.status_code == 404
    assert response.json()["detail"] == "no mock endpoint for GET /customers"


def test_api_key_server_requires_key_and_accepts_valid_key(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    _create_playground(conn, slug="secure", auth_mode="api_key")
    key = _create_api_key(conn)

    missing_key = client.get("/mock/secure/orders")
    with_key = client.get("/mock/secure/orders", headers={"X-API-Key": key})

    assert missing_key.status_code == 401
    assert missing_key.headers["WWW-Authenticate"] == "Bearer"
    assert with_key.status_code == 200, with_key.text


def test_open_auth_server_needs_no_key(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_playground(conn, slug="open", auth_mode="none")

    response = client.get("/mock/open/orders")

    assert response.status_code == 200, response.text


def test_malformed_json_body_returns_400(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_playground(conn)

    response = client.post(
        "/mock/zava-test/orders",
        content="{",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "request body must be valid JSON"}


def test_traffic_row_is_written_with_rest_kind(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    _create_playground(conn)

    response = client.get("/mock/zava-test/orders?status=open")

    assert response.status_code == 200, response.text
    traffic = store.get_traffic(conn)
    assert traffic[0]["kind"] == "rest"
    assert traffic[0]["tool_name"] == "list_orders"
    assert traffic[0]["request"] == {"status": "open"}
