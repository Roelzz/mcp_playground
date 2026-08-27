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
def client(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("AUTH_DISABLED", "1")
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[auth.get_conn] = lambda: conn
    with TestClient(app) as test_client:
        yield test_client


def _create_source(conn: sqlite3.Connection, slug: str = "hr-core") -> dict[str, Any]:
    server = service.create_server(conn, slug, "HR Core", "Core HR training server", "none")
    employees = service.create_dataset(
        conn,
        int(server["id"]),
        "employees",
        "id",
        [
            {"id": 1, "name": "Ada", "role": "Engineer"},
            {"id": 2, "name": "Grace", "role": "Manager"},
        ],
    )
    policies = service.create_dataset(
        conn,
        int(server["id"]),
        "policies",
        "id",
        [{"id": 10, "title": "Leave"}],
    )
    service.add_rows(conn, int(employees["id"]), [{"id": 3, "name": "Linus", "role": "Trainer"}])
    service.create_endpoint(
        conn,
        int(server["id"]),
        "/employees",
        "GET",
        "list_employees",
        dataset_id=int(employees["id"]),
        summary_fields=["id", "name"],
    )
    service.create_endpoint(
        conn,
        int(server["id"]),
        "/employees/{id}",
        "GET",
        "get_employee",
        dataset_id=int(employees["id"]),
    )
    service.create_endpoint(
        conn,
        int(server["id"]),
        "/employees/{id}",
        "PATCH",
        "update_employee",
        dataset_id=int(employees["id"]),
    )
    service.create_endpoint(
        conn,
        int(server["id"]),
        "/policies",
        "GET",
        "list_policies",
        dataset_id=int(policies["id"]),
    )
    return service.get_server(conn, int(server["id"]))


def _clean_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in row.items() if key != "_row_id"} for row in rows]


def _dataset_counts(server: dict[str, Any]) -> dict[str, tuple[int, int]]:
    return {
        str(dataset["key"]): (int(dataset["row_count"]), int(dataset["seed_count"]))
        for dataset in server["datasets"]
    }


def _endpoint_tool_names(server: dict[str, Any]) -> list[str]:
    return sorted(str(endpoint["tool_name"]) for endpoint in server["endpoints"])


def test_clone_copies_datasets_rows_seeds_and_endpoints(conn: sqlite3.Connection) -> None:
    source = _create_source(conn)

    clone = service.clone_server(conn, int(source["id"]), "hr-team01", "HR — Team 01")

    assert clone["slug"] == "hr-team01"
    assert clone["name"] == "HR — Team 01"
    assert [dataset["key"] for dataset in clone["datasets"]] == [
        dataset["key"] for dataset in source["datasets"]
    ]
    assert _dataset_counts(clone) == _dataset_counts(source)
    assert _endpoint_tool_names(clone) == _endpoint_tool_names(source)


def test_clone_endpoint_dataset_ids_point_at_cloned_datasets(conn: sqlite3.Connection) -> None:
    source = _create_source(conn)

    clone = service.clone_server(conn, int(source["id"]), "hr-team01")

    source_datasets = {int(dataset["id"]): dataset for dataset in source["datasets"]}
    clone_datasets = {int(dataset["id"]): dataset for dataset in clone["datasets"]}
    source_dataset_ids = set(source_datasets)
    clone_dataset_ids = set(clone_datasets)
    source_endpoints = {endpoint["tool_name"]: endpoint for endpoint in source["endpoints"]}

    for endpoint in clone["endpoints"]:
        dataset_id = int(endpoint["dataset_id"])
        source_endpoint = source_endpoints[endpoint["tool_name"]]
        assert dataset_id in clone_dataset_ids
        assert dataset_id not in source_dataset_ids
        assert clone_datasets[dataset_id]["key"] == source_datasets[
            int(source_endpoint["dataset_id"])
        ]["key"]


def test_mutating_clone_row_does_not_change_source(conn: sqlite3.Connection) -> None:
    source = _create_source(conn)
    service.clone_server(conn, int(source["id"]), "hr-team01")

    updated = service.call_tool(
        conn,
        "hr-team01",
        "update_employee",
        {"id": 1, "role": "Clone only"},
    )
    source_row = service.call_tool(conn, "hr-core", "get_employee", {"id": 1})

    assert updated["role"] == "Clone only"
    assert source_row["role"] == "Engineer"


def test_clone_existing_slug_conflicts_and_api_returns_409(
    conn: sqlite3.Connection, client: TestClient
) -> None:
    source = _create_source(conn)
    service.create_server(conn, "hr-team01", "Existing")
    before = len(store.list_servers(conn))

    with pytest.raises(service.Conflict):
        service.clone_server(conn, int(source["id"]), "hr-team01")

    response = client.post(
        f"/api/servers/{source['id']}/clone",
        json={"slug": "hr-team01", "name": "Duplicate"},
    )

    assert response.status_code == 409
    assert len(store.list_servers(conn)) == before


def test_bulk_clone_creates_expected_zero_padded_slugs(conn: sqlite3.Connection) -> None:
    source = _create_source(conn)

    result = service.bulk_clone_server(conn, int(source["id"]), "hr-team", 3)

    assert [server["slug"] for server in result["created"]] == [
        "hr-team01",
        "hr-team02",
        "hr-team03",
    ]
    assert [server["name"] for server in result["created"]] == [
        "HR Core 01",
        "HR Core 02",
        "HR Core 03",
    ]


def test_bulk_clone_conflict_creates_nothing(conn: sqlite3.Connection) -> None:
    source = _create_source(conn)
    service.create_server(conn, "hr-team02", "Existing")
    before_count = len(store.list_servers(conn))

    with pytest.raises(service.Conflict):
        service.bulk_clone_server(conn, int(source["id"]), "hr-team", 3)

    assert len(store.list_servers(conn)) == before_count
    assert store.get_server_by_slug(conn, "hr-team01") is None
    assert store.get_server_by_slug(conn, "hr-team03") is None


def test_catalog_returns_counts_matching_seeded_server(
    conn: sqlite3.Connection, client: TestClient
) -> None:
    source = _create_source(conn)

    response = client.get("/api/catalog")

    assert response.status_code == 200, response.text
    catalog_server = next(server for server in response.json() if server["slug"] == source["slug"])
    assert catalog_server["dataset_count"] == len(source["datasets"])
    assert catalog_server["endpoint_count"] == len(source["endpoints"])
    assert catalog_server["row_count"] == sum(
        int(dataset["row_count"]) for dataset in source["datasets"]
    )


def test_cloned_server_is_live_over_real_app_rest(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_DISABLED", "1")
    import main

    source = _create_source(conn)
    main.app.dependency_overrides[auth.get_conn] = lambda: conn
    client = TestClient(main.app)
    try:
        clone = client.post(
            f"/api/servers/{source['id']}/clone",
            json={"slug": "hr-live", "name": "HR Live"},
        )
        response = client.get("/mock/hr-live/employees")
    finally:
        main.app.dependency_overrides.pop(auth.get_conn, None)
        client.close()

    assert clone.status_code == 201, clone.text
    assert response.status_code == 200, response.text
    assert response.json() == [
        {"id": 1, "name": "Ada"},
        {"id": 2, "name": "Grace"},
        {"id": 3, "name": "Linus"},
    ]
