import json
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path
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
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("AUTH_DISABLED", "1")
    conn = db.init_db(str(tmp_path / "test.db"))
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(portability.router)
    app.dependency_overrides[get_conn] = lambda: conn
    with TestClient(app) as c:
        yield c


def _conn(client: TestClient) -> sqlite3.Connection:
    return client.app.dependency_overrides[get_conn]()


def _build_server(
    conn: sqlite3.Connection, slug: str = "acme", auth_mode: str = "none"
) -> dict[str, Any]:
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
        conn, server["id"], "/orders", "POST", "create_order", "Create order", orders["id"]
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


def _operation(swagger: dict[str, Any], operation_id: str) -> dict[str, Any]:
    for path_item in swagger["paths"].values():
        for operation in path_item.values():
            if operation["operationId"] == operation_id:
                return operation
    raise AssertionError(f"operation {operation_id} not found")


def _parameter(operation: dict[str, Any], name: str) -> dict[str, Any]:
    for parameter in operation["parameters"]:
        if parameter["name"] == name:
            return parameter
    raise AssertionError(f"parameter {name} not found")


def _body_schema(swagger: dict[str, Any], operation_id: str) -> dict[str, Any]:
    operation = _operation(swagger, operation_id)
    ref = _parameter(operation, "body")["schema"]["$ref"].rsplit("/", 1)[-1]
    return swagger["definitions"][ref]


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
    assert swagger["basePath"] == "/mock/acme"
    assert swagger["schemes"] == ["http"]
    assert set(swagger["paths"]) == {
        "/empty",
        "/orders",
        "/orders/search",
        "/orders/{order_number}",
    }
    assert all(not path.endswith("/call") for path in swagger["paths"])
    assert set(swagger["paths"]["/orders"]) == {"get", "post"}

    operations = [
        operation
        for path_item in swagger["paths"].values()
        for operation in path_item.values()
    ]
    operation_ids = [operation["operationId"] for operation in operations]
    assert len(operation_ids) == 7
    assert len(operation_ids) == len(set(operation_ids))
    assert all(
        re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", operation_id) for operation_id in operation_ids
    )
    assert set(operation_ids) == {
        "create_order",
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

    list_operation = _operation(swagger, "list_orders")
    limit_param = _parameter(list_operation, "limit")
    assert limit_param["in"] == "query"
    assert limit_param["required"] is False
    assert limit_param["type"] == "integer"
    assert _parameter(list_operation, "status")["in"] == "query"

    search_operation = _operation(swagger, "search_orders")
    q_param = _parameter(search_operation, "q")
    assert q_param["in"] == "query"
    assert q_param["description"] == "Text to search for across string fields"

    get_operation = _operation(swagger, "get_order")
    path_param = _parameter(get_operation, "order_number")
    assert path_param["in"] == "path"
    assert path_param["required"] is True
    assert path_param["type"] == "integer"
    assert "schema" not in path_param

    create_operation = _operation(swagger, "create_order")
    create_body = _parameter(create_operation, "body")
    assert create_body["in"] == "body"
    assert create_body["required"] is True
    assert create_operation["responses"]["201"]["schema"] == {"$ref": "#/definitions/OrdersItem"}

    update_schema = _body_schema(swagger, "update_order")
    assert "order_number" not in update_schema["properties"]
    assert update_schema["additionalProperties"] is False

    delete_operation = _operation(swagger, "delete_order")
    assert all(parameter["in"] != "body" for parameter in delete_operation["parameters"])
    delete_schema = delete_operation["responses"]["200"]["schema"]
    assert delete_schema["properties"]["deleted"]["type"] == "boolean"
    assert delete_schema["properties"]["order_number"]["type"] == "integer"

    order_def = swagger["definitions"]["OrdersItem"]
    assert order_def["properties"]["order_number"]["type"] == "integer"
    assert order_def["properties"]["status"]["type"] == "string"
    assert order_def["properties"]["total"]["type"] == "number"
    assert order_def["properties"]["active"]["type"] == "boolean"
    assert swagger["definitions"]["EmptyItem"]["type"] == "object"

    assert json.loads(json.dumps(swagger)) == swagger


def test_swagger_security_matches_server_auth_mode(client: TestClient) -> None:
    conn = _conn(client)
    none_server = _build_server(conn, "none-server", "none")
    key_server = _build_server(conn, "key-server", "api_key")

    none_swagger = portability.build_swagger(conn, none_server["id"], "https://example.com")
    key_swagger = portability.build_swagger(conn, key_server["id"], "https://example.com")
    expected = {"api_key": {"type": "apiKey", "in": "header", "name": "X-API-Key"}}
    assert "securityDefinitions" not in none_swagger
    assert "security" not in none_swagger
    assert key_swagger["securityDefinitions"] == expected
    assert key_swagger["security"] == [{"api_key": []}]


def test_swagger_duplicate_operation_id_still_raises(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _conn(client)
    server = _build_server(conn)
    original = service.list_endpoints

    def duplicate_endpoints(
        endpoint_conn: sqlite3.Connection, endpoint_server_id: int
    ) -> list[dict[str, Any]]:
        endpoints = original(endpoint_conn, endpoint_server_id)
        endpoints.append(dict(endpoints[0]))
        return endpoints

    monkeypatch.setattr(portability.service, "list_endpoints", duplicate_endpoints)

    with pytest.raises(service.ServiceError, match="duplicate operationId 'create_order'"):
        portability.build_swagger(conn, server["id"], "https://example.com")


def test_swagger_missing_host_still_raises(client: TestClient) -> None:
    conn = _conn(client)
    server = _build_server(conn)

    with pytest.raises(service.ServiceError, match="PUBLIC_BASE_URL must include a host"):
        portability.build_swagger(conn, server["id"], "")


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
    assert summary["endpoints"] == 7
    assert len(service.list_datasets(conn, imported["id"])) == 2
    assert len(service.list_endpoints(conn, imported["id"])) == 7

    imported_datasets = service.list_datasets(conn, imported["id"])
    imported_orders = next(dataset for dataset in imported_datasets if dataset["key"] == "orders")
    details = service.get_dataset(conn, imported_orders["id"])
    assert details["row_count"] == 1
    assert details["seed_count"] == 2

    endpoints = service.list_endpoints(conn, imported["id"])
    assert sorted(endpoint["tool_name"] for endpoint in endpoints) == [
        "create_order",
        "delete_order",
        "get_order",
        "list_empty",
        "list_orders",
        "search_orders",
        "update_order",
    ]
    assert {endpoint["tool_name"]: endpoint["tool_type"] for endpoint in endpoints} == {
        "create_order": "create",
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
