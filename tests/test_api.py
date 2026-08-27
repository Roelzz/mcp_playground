import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api
import db
import store
from auth import get_conn


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "1")
    conn = db.init_db(str(tmp_path / "test.db"))
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[get_conn] = lambda: conn
    with TestClient(app) as c:
        yield c


def _post_server(client: TestClient, slug: str = "acme") -> dict:
    response = client.post("/api/servers", json={"slug": slug, "name": slug.title()})
    assert response.status_code == 201, response.text
    return response.json()


def _post_dataset(client: TestClient, server_id: int, rows: list[dict]) -> dict:
    response = client.post(
        f"/api/servers/{server_id}/datasets",
        json={"key": "orders", "id_field": "id", "rows": rows},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _post_endpoint(
    client: TestClient,
    server_id: int,
    dataset_id: int,
    method: str,
    path: str,
    tool_name: str,
) -> dict:
    response = client.post(
        f"/api/servers/{server_id}/endpoints",
        json={
            "method": method,
            "path": path,
            "tool_name": tool_name,
            "dataset_id": dataset_id,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_full_happy_path_round_trip_over_http(client: TestClient) -> None:
    server = _post_server(client)
    rows = [{"id": 1, "status": "open", "name": "Alpha"}]
    dataset = _post_dataset(client, server["id"], rows)
    _post_endpoint(client, server["id"], dataset["id"], "GET", "/orders", "list_orders")
    _post_endpoint(client, server["id"], dataset["id"], "GET", "/orders/{id}", "get_order")
    _post_endpoint(client, server["id"], dataset["id"], "PATCH", "/orders/{id}", "update_order")

    fetched_server = client.get(f"/api/servers/{server['id']}")
    assert fetched_server.status_code == 200, fetched_server.text
    assert len(fetched_server.json()["datasets"]) == 1
    assert len(fetched_server.json()["endpoints"]) == 3

    listed = client.post("/api/servers/acme/tools/list_orders/call", json={})
    assert listed.status_code == 200, listed.text
    assert listed.json()["result"] == rows

    updated = client.post(
        "/api/servers/acme/tools/update_order/call",
        json={"id": 1, "status": "closed"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["result"]["status"] == "closed"

    fetched = client.post("/api/servers/acme/tools/get_order/call", json={"id": 1})
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["result"]["status"] == "closed"

    reset = client.post(f"/api/datasets/{dataset['id']}/reset-to-seed")
    assert reset.status_code == 200, reset.text
    assert reset.json()["row_count"] == 1

    restored = client.post("/api/servers/acme/tools/get_order/call", json={"id": 1})
    assert restored.status_code == 200, restored.text
    assert restored.json()["result"]["status"] == "open"


def test_service_errors_map_to_http_status_codes(client: TestClient) -> None:
    missing = client.get("/api/servers/9999")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "server 9999 not found"

    _post_server(client, "acme")
    duplicate = client.post("/api/servers", json={"slug": "acme", "name": "Duplicate"})
    assert duplicate.status_code == 409
    assert "already exists" in duplicate.json()["detail"]

    invalid = client.post("/api/servers", json={"slug": "Bad Slug", "name": "Bad"})
    assert invalid.status_code == 400
    assert "invalid slug" in invalid.json()["detail"]


def test_every_registered_route_is_reachable(client: TestClient) -> None:
    assert client.get("/api/servers").status_code == 200
    server = _post_server(client)

    assert client.get(f"/api/servers/{server['id']}").status_code == 200
    patched_server = client.patch(
        f"/api/servers/{server['id']}",
        json={"description": "updated"},
    )
    assert patched_server.status_code == 200, patched_server.text

    assert client.get(f"/api/servers/{server['id']}/datasets").status_code == 200
    dataset = _post_dataset(client, server["id"], [{"id": 1, "name": "Alpha"}])

    assert client.get(f"/api/datasets/{dataset['id']}").status_code == 200
    patched_dataset = client.patch(f"/api/datasets/{dataset['id']}", json={"key": "orders-v2"})
    assert patched_dataset.status_code == 200, patched_dataset.text
    assert client.get(f"/api/datasets/{dataset['id']}/rows").status_code == 200

    added_rows = client.post(f"/api/datasets/{dataset['id']}/rows", json={"rows": [{"id": 2}]})
    assert added_rows.status_code == 200, added_rows.text
    replaced_rows = client.put(f"/api/datasets/{dataset['id']}/rows", json={"rows": [{"id": 3}]})
    assert replaced_rows.status_code == 200, replaced_rows.text
    assert client.post(f"/api/datasets/{dataset['id']}/save-as-seed").status_code == 200
    assert client.post(f"/api/datasets/{dataset['id']}/reset-to-seed").status_code == 200

    assert client.get(f"/api/servers/{server['id']}/endpoints").status_code == 200
    endpoint = _post_endpoint(
        client,
        server["id"],
        dataset["id"],
        "GET",
        "/orders",
        "list_orders",
    )
    assert client.get(f"/api/endpoints/{endpoint['id']}").status_code == 200
    patched_endpoint = client.patch(
        f"/api/endpoints/{endpoint['id']}",
        json={"description": "endpoint updated"},
    )
    assert patched_endpoint.status_code == 200, patched_endpoint.text

    call = client.post("/api/servers/acme/tools/list_orders/call", json={})
    assert call.status_code == 200, call.text

    assert client.get("/api/traffic").status_code == 200
    assert client.delete("/api/traffic").status_code == 204

    assert client.get("/api/llm-endpoints").status_code == 200
    llm_response = client.post("/api/llm-endpoints", json={"slug": "mock", "name": "Mock"})
    assert llm_response.status_code == 201, llm_response.text
    llm = llm_response.json()
    assert client.get(f"/api/llm-endpoints/{llm['id']}").status_code == 200
    patched_llm = client.patch(
        f"/api/llm-endpoints/{llm['id']}",
        json={"description": "llm updated"},
    )
    assert patched_llm.status_code == 200, patched_llm.text
    responses = client.put(f"/api/llm-endpoints/{llm['id']}/responses", json=[{"response": "ok"}])
    assert responses.status_code == 200, responses.text
    assert client.delete(f"/api/llm-endpoints/{llm['id']}").status_code == 204

    assert client.delete(f"/api/endpoints/{endpoint['id']}").status_code == 204
    assert client.delete(f"/api/datasets/{dataset['id']}").status_code == 204
    assert client.delete(f"/api/servers/{server['id']}").status_code == 204


def test_service_not_found_404_detail_proves_route_exists(client: TestClient) -> None:
    response = client.get("/api/datasets/9999")

    assert response.status_code == 404
    assert response.json()["detail"] == "dataset 9999 not found"


def test_patch_partial_update_keeps_existing_fields(client: TestClient) -> None:
    server = client.post(
        "/api/servers",
        json={"slug": "acme", "name": "Acme", "description": "initial"},
    ).json()

    response = client.patch(f"/api/servers/{server['id']}", json={"description": "changed"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["slug"] == "acme"
    assert body["name"] == "Acme"
    assert body["description"] == "changed"
    assert body["auth_mode"] == "none"


def test_traffic_routes_list_and_clear(client: TestClient) -> None:
    conn = client.app.dependency_overrides[get_conn]()
    store.log_call(conn, "mcp", "ok", target_slug="acme", tool_name="list_orders")

    listed = client.get("/api/traffic")
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1

    deleted = client.delete("/api/traffic")
    assert deleted.status_code == 204, deleted.text
    assert client.get("/api/traffic").json() == []
