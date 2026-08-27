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
        operation for path_item in swagger["paths"].values() for operation in path_item.values()
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
    assert bundle["version"] == 2
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


# ---------------------------------------------------------------------------
# Helpers for relationship / expand tests
# ---------------------------------------------------------------------------


def _build_server_with_relationships(
    conn: sqlite3.Connection,
    slug: str = "acme-with-rels",
) -> tuple[dict[str, Any], dict[str, Any]]:
    server = _build_server(conn, slug)
    datasets = service.list_datasets(conn, server["id"])
    orders = next(d for d in datasets if d["key"] == "orders")
    empty = next(d for d in datasets if d["key"] == "empty")
    rel = service.create_relationship(
        conn,
        server["id"],
        name="orders_empty",
        source_dataset_id=orders["id"],
        source_field="order_number",
        target_dataset_id=empty["id"],
        target_field="id",
        relation_type="many_to_one",
        expand_name="empty_item",
        inverse_expand_name="order_list",
        required=False,
        description="Test relationship",
    )
    return server, rel


def _has_ref_cycle(swagger: dict[str, Any]) -> bool:
    definitions = swagger.get("definitions", {})

    def collect_refs(obj: Any) -> list[str]:
        refs: list[str] = []
        if isinstance(obj, dict):
            if "$ref" in obj and isinstance(obj["$ref"], str) and obj["$ref"].startswith(
                "#/definitions/"
            ):
                refs.append(obj["$ref"][len("#/definitions/") :])
            for v in obj.values():
                refs.extend(collect_refs(v))
        elif isinstance(obj, list):
            for item in obj:
                refs.extend(collect_refs(item))
        return refs

    adj = {name: collect_refs(defn) for name, defn in definitions.items()}

    def dfs(node: str, visited: set[str], stack: set[str]) -> bool:
        visited.add(node)
        stack.add(node)
        for neighbour in adj.get(node, []):
            if neighbour in stack:
                return True
            if neighbour not in visited and dfs(neighbour, visited, stack):
                return True
        stack.discard(node)
        return False

    visited: set[str] = set()
    for name in definitions:
        if name not in visited and dfs(name, visited, set()):
            return True
    return False


# ---------------------------------------------------------------------------
# Phase 2: bundle export / import with relationships
# ---------------------------------------------------------------------------


def test_bundle_export_includes_relationships_by_dataset_key(client: TestClient) -> None:
    conn = _conn(client)
    server, rel = _build_server_with_relationships(conn)

    bundle = portability.build_bundle(conn, server["id"])

    assert bundle["version"] == 2
    assert "relationships" in bundle
    rels = bundle["relationships"]
    assert len(rels) == 1
    exported = rels[0]
    assert exported["name"] == "orders_empty"
    assert exported["source_dataset_key"] == "orders"
    assert exported["target_dataset_key"] == "empty"
    assert exported["expand_name"] == "empty_item"
    assert exported["inverse_expand_name"] == "order_list"
    assert exported["relation_type"] == "many_to_one"
    assert exported["required"] is False
    # Must not contain numeric IDs
    _assert_no_key(bundle, {"id", "server_id", "dataset_id", "_row_id"})
    assert "source_dataset_id" not in exported
    assert "target_dataset_id" not in exported


def test_bundle_export_server_with_no_relationships(client: TestClient) -> None:
    conn = _conn(client)
    server = _build_server(conn)

    bundle = portability.build_bundle(conn, server["id"])

    assert bundle["version"] == 2
    assert bundle["relationships"] == []


def test_v1_bundle_imports_cleanly(client: TestClient) -> None:
    conn = _conn(client)
    v1_bundle = {
        "format": "mcp-playground-server",
        "version": 1,
        "exported_at": "2025-01-01T00:00:00+00:00",
        "server": {"slug": "v1-test", "name": "V1 Test", "description": "", "auth_mode": "none"},
        "datasets": [],
        "endpoints": [],
    }

    summary = portability.import_bundle(conn, v1_bundle)

    assert summary["server"]["slug"] == "v1-test"
    imported_rels = service.list_relationships(conn, summary["server"]["id"])
    assert imported_rels == []


def test_v2_round_trip_preserves_relationships(client: TestClient) -> None:
    conn = _conn(client)
    server, _rel = _build_server_with_relationships(conn)

    bundle = portability.build_bundle(conn, server["id"])
    assert bundle["version"] == 2

    summary = portability.import_bundle(conn, bundle, slug="acme-copy")
    imported_server = summary["server"]

    imported_rels = service.list_relationships(conn, imported_server["id"])
    assert len(imported_rels) == 1
    imported_rel = imported_rels[0]
    assert imported_rel["name"] == "orders_empty"
    assert imported_rel["expand_name"] == "empty_item"
    assert imported_rel["inverse_expand_name"] == "order_list"
    assert imported_rel["relation_type"] == "many_to_one"
    assert imported_rel["source_field"] == "order_number"
    assert imported_rel["target_field"] == "id"


def test_imported_relationship_ids_differ_and_point_at_new_datasets(
    client: TestClient,
) -> None:
    conn = _conn(client)
    server, original_rel = _build_server_with_relationships(conn)

    bundle = portability.build_bundle(conn, server["id"])
    summary = portability.import_bundle(conn, bundle, slug="acme-copy-2")
    imported_server = summary["server"]

    # Relationship ID must differ from the original
    imported_rels = service.list_relationships(conn, imported_server["id"])
    assert len(imported_rels) == 1
    imported_rel = imported_rels[0]
    assert imported_rel["id"] != original_rel["id"]

    # source/target dataset IDs must point at the NEW server's datasets
    imported_datasets = service.list_datasets(conn, imported_server["id"])
    imported_dataset_ids = {d["id"] for d in imported_datasets}
    original_dataset_ids = {
        d["id"] for d in service.list_datasets(conn, server["id"])
    }
    assert imported_rel["source_dataset_id"] in imported_dataset_ids
    assert imported_rel["target_dataset_id"] in imported_dataset_ids
    assert imported_rel["source_dataset_id"] not in original_dataset_ids
    assert imported_rel["target_dataset_id"] not in original_dataset_ids


def test_bundle_unknown_dataset_key_fails_atomically(client: TestClient) -> None:
    conn = _conn(client)
    server = _build_server(conn, "atomic-test")

    bundle = portability.build_bundle(conn, server["id"])
    bundle["relationships"] = [
        {
            "name": "bad_rel",
            "source_dataset_key": "nonexistent",
            "source_field": "id",
            "target_dataset_key": "orders",
            "target_field": "order_number",
            "relation_type": "many_to_one",
            "expand_name": "bad_expand",
            "inverse_expand_name": None,
            "required": False,
            "description": "",
        }
    ]

    with pytest.raises(service.ServiceError, match="unknown dataset key"):
        portability.import_bundle(conn, bundle, slug="should-not-exist")

    # No orphan server should be left behind
    all_slugs = [s["slug"] for s in service.list_servers(conn)]
    assert "should-not-exist" not in all_slugs


# ---------------------------------------------------------------------------
# Phase 5: Swagger expand parameter
# ---------------------------------------------------------------------------


def test_swagger_read_operations_have_expand_parameter(client: TestClient) -> None:
    conn = _conn(client)
    server = _build_server(conn)
    swagger = portability.build_swagger(conn, server["id"], "http://localhost:2009")

    for op_id in ("list_orders", "search_orders", "get_order", "list_empty"):
        op = _operation(swagger, op_id)
        param = _parameter(op, "expand")
        assert param["in"] == "query"
        assert param["required"] is False
        assert param["type"] == "string"
        assert param["x-ms-visibility"] == "advanced"
        assert param["x-ms-summary"]


def test_swagger_write_operations_no_expand_parameter(client: TestClient) -> None:
    conn = _conn(client)
    server = _build_server(conn)
    swagger = portability.build_swagger(conn, server["id"], "http://localhost:2009")

    for op_id in ("create_order", "update_order", "delete_order"):
        op = _operation(swagger, op_id)
        param_names = [p["name"] for p in op["parameters"]]
        assert "expand" not in param_names


def test_swagger_no_recursive_refs(client: TestClient) -> None:
    conn = _conn(client)
    server = _build_server(conn)
    swagger = portability.build_swagger(conn, server["id"], "http://localhost:2009")

    assert not _has_ref_cycle(swagger)
