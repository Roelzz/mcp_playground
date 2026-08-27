import json
import re
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api
import db
import portability
import service
from auth import get_conn


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "1")
    conn = db.init_db(str(tmp_path / "test.db"))
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(portability.router)
    app.dependency_overrides[get_conn] = lambda: conn
    with TestClient(app) as c:
        yield c


def _conn(client: TestClient):
    return client.app.dependency_overrides[get_conn]()


def _build_server(conn, slug: str = "acme", auth_mode: str = "none") -> dict[str, Any]:
    server = service.create_server(conn, slug, slug.title(), "Demo server", auth_mode)
    orders = service.create_dataset(
        conn,
        server["id"],
        "orders",
        "order_number",
        [
            {
                "order_number": 1,
                "status": "open",
                "total": 12.5,
                "active": True,
                "name": "Alpha",
            },
            {
                "order_number": 2,
                "status": "closed",
                "total": 7,
                "active": False,
                "name": "Beta",
            },
        ],
    )
    empty = service.create_dataset(conn, server["id"], "empty", "id", [])
    service.create_endpoint(
        conn, server["id"], "/orders", "GET", "list_orders", "List orders", orders["id"]
    )
    service.create_endpoint(
        conn, server["id"], "/orders/search", "GET", "search_orders", "Search orders", orders["id"]
    )
    service.create_endpoint(
        conn,
        server["id"],
        "/orders/{order_number}",
        "GET",
        "get_order",
        "Get order",
        orders["id"],
    )
    service.create_endpoint(
        conn,
        server["id"],
        "/orders/{order_number}",
        "PATCH",
        "update_order",
        "Update order",
        orders["id"],
    )
    service.create_endpoint(
        conn,
        server["id"],
        "/orders/{order_number}",
        "DELETE",
        "delete_order",
        "Delete order",
        orders["id"],
    )
    service.create_endpoint(
        conn, server["id"], "/empty", "GET", "list_empty", "List empty", empty["id"]
    )
    return server


def _param_schema(swagger: dict[str, Any], operation_id: str) -> dict[str, Any]:
    for path_item in swagger["paths"].values():
        operation = path_item["post"]
        if operation["operationId"] == operation_id:
            ref = operation["parameters"][0]["schema"]["$ref"].rsplit("/", 1)[-1]
            return swagger["definitions"][ref]
    raise AssertionError(f"operation {operation_id} not found")


def _assert_no_key(value: Any, forbidden: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert key not in forbidden
            _assert_no_key(child, forbidden)
    elif isinstance(value, list):
        for child in value:
            _assert_no_key(child, forbidden)


def test_swagger_shape_operations_parameters_and_definitions(client: TestClient) -> None:
    conn = _conn(client)
    server = _build_server(conn)

    swagger = portability.build_swagger(conn, server["id"], "http://localhost:2009")

    assert swagger["swagger"] == "2.0"
    assert swagger["info"] == {"title": "Acme", "description": "Demo server", "version": "1.0.0"}
    assert swagger["host"] == "localhost:2009"
    assert swagger["basePath"] == "/api/servers/acme/tools"
    assert swagger["schemes"] == ["http"]

    operations = [path_item["post"] for path_item in swagger["paths"].values()]
    operation_ids = [operation["operationId"] for operation in operations]
    assert len(operation_ids) == 6
    assert len(operation_ids) == len(set(operation_ids))
    assert all(
        re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", operation_id) for operation_id in operation_ids
    )
    assert set(operation_ids) == {
        "list_orders",
        "search_orders",
        "get_order",
        "update_order",
        "delete_order",
        "list_empty",
    }
    for operation in operations:
        assert operation["x-ms-summary"]
        for parameter in operation["parameters"]:
            assert parameter["x-ms-summary"]

    assert "limit" in _param_schema(swagger, "list_orders")["properties"]
    search_properties = _param_schema(swagger, "search_orders")["properties"]
    assert "q" in search_properties
    assert "status" in search_properties

    for operation_id in ("get_order", "update_order", "delete_order"):
        schema = _param_schema(swagger, operation_id)
        assert "order_number" in schema["properties"]
        assert "order_number" in schema["required"]

    order_def = swagger["definitions"]["OrdersItem"]
    assert order_def["properties"]["order_number"]["type"] == "integer"
    assert order_def["properties"]["status"]["type"] == "string"
    assert order_def["properties"]["total"]["type"] == "number"
    assert order_def["properties"]["active"]["type"] == "boolean"
    assert swagger["definitions"]["EmptyItem"]["type"] == "object"

    assert json.loads(json.dumps(swagger)) == swagger


def test_swagger_always_requires_api_key(client: TestClient) -> None:
    conn = _conn(client)
    none_server = _build_server(conn, "none-server", "none")
    key_server = _build_server(conn, "key-server", "api_key")

    none_swagger = portability.build_swagger(conn, none_server["id"], "https://example.com")
    key_swagger = portability.build_swagger(conn, key_server["id"], "https://example.com")

    expected = {"api_key": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
    # The /api call route is admin-gated no matter what the MCP server's auth_mode is.
    assert none_swagger["securityDefinitions"] == expected
    assert none_swagger["security"] == [{"api_key": []}]
    assert key_swagger["securityDefinitions"] == expected
    assert key_swagger["security"] == [{"api_key": []}]


def test_export_routes_download_and_missing_server(client: TestClient) -> None:
    conn = _conn(client)
    server = _build_server(conn)

    swagger = client.get(f"/api/servers/{server['id']}/swagger?download=1")
    assert swagger.status_code == 200, swagger.text
    assert swagger.headers["content-disposition"] == 'attachment; filename="acme-swagger.json"'

    bundle = client.get(f"/api/servers/{server['id']}/export?download=1")
    assert bundle.status_code == 200, bundle.text
    assert bundle.headers["content-disposition"] == 'attachment; filename="acme-bundle.json"'

    assert client.get("/api/servers/9999/swagger").status_code == 404
    assert client.get("/api/servers/9999/export").status_code == 404


def test_bundle_export_strips_ids_row_ids_and_secrets(client: TestClient) -> None:
    conn = _conn(client)
    server = _build_server(conn)

    bundle = portability.build_bundle(conn, server["id"])
    serialized = json.dumps(bundle)

    assert bundle["format"] == "mcp-playground-server"
    assert bundle["version"] == 1
    _assert_no_key(bundle, {"id", "server_id", "dataset_id", "_row_id"})
    for forbidden in ("api_key", "secret", "token", "password", "key_hash"):
        assert forbidden not in serialized.lower()


def test_bundle_round_trip_preserves_counts_rows_seed_rows_and_endpoints(
    client: TestClient,
) -> None:
    conn = _conn(client)
    server = _build_server(conn)
    datasets = service.list_datasets(conn, server["id"])
    orders = next(dataset for dataset in datasets if dataset["key"] == "orders")
    service.save_as_seed(conn, orders["id"])
    service.replace_rows(conn, orders["id"], [{"order_number": 99, "status": "current"}])

    bundle = portability.build_bundle(conn, server["id"])
    summary = portability.import_bundle(conn, bundle, slug="acme-copy")
    imported = summary["server"]

    assert summary["datasets"] == 2
    assert summary["endpoints"] == 6
    assert len(service.list_datasets(conn, imported["id"])) == 2
    assert len(service.list_endpoints(conn, imported["id"])) == 6

    imported_datasets = service.list_datasets(conn, imported["id"])
    imported_orders = next(dataset for dataset in imported_datasets if dataset["key"] == "orders")
    details = service.get_dataset(conn, imported_orders["id"])
    assert details["row_count"] == 1
    assert details["seed_count"] == 2

    endpoints = service.list_endpoints(conn, imported["id"])
    assert sorted(endpoint["tool_name"] for endpoint in endpoints) == [
        "delete_order",
        "get_order",
        "list_empty",
        "list_orders",
        "search_orders",
        "update_order",
    ]
    assert {endpoint["tool_name"]: endpoint["tool_type"] for endpoint in endpoints} == {
        "list_orders": "list",
        "search_orders": "search",
        "get_order": "get",
        "update_order": "update",
        "delete_order": "delete",
        "list_empty": "list",
    }


def test_import_route_conflict_and_bad_bundle_errors(client: TestClient) -> None:
    conn = _conn(client)
    server = _build_server(conn)
    bundle = portability.build_bundle(conn, server["id"])

    duplicate = client.post("/api/servers/import", json=bundle)
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]

    bad_format = client.post("/api/servers/import", json={"format": "wrong", "version": 1})
    assert bad_format.status_code == 400
    assert "format" in bad_format.json()["detail"]

    bad_version = client.post(
        "/api/servers/import",
        json={
            "format": "mcp-playground-server",
            "version": 999,
            "server": {},
            "datasets": [],
            "endpoints": [],
        },
    )
    assert bad_version.status_code == 400
    assert "version" in bad_version.json()["detail"]

    wrapped = client.post("/api/servers/import", json={"bundle": bundle, "slug": "wrapped-copy"})
    assert wrapped.status_code == 201, wrapped.text
    assert wrapped.json()["server"]["slug"] == "wrapped-copy"
