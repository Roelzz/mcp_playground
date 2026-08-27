import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

import auth
import db
import main
import seed
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
    main.app.dependency_overrides[auth.get_conn] = lambda: conn
    test_client = TestClient(main.app, raise_server_exceptions=False)
    try:
        yield test_client
    finally:
        main.app.dependency_overrides.pop(auth.get_conn, None)
        test_client.close()


def _create_server(
    conn: sqlite3.Connection,
    slug: str,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    server = service.create_server(conn, slug, slug.title(), "Training server", "none")
    dataset = service.create_dataset(
        conn,
        int(server["id"]),
        "items",
        "id",
        rows if rows is not None else [{"id": 1, "name": "Alpha"}],
    )
    service.create_endpoint(
        conn,
        int(server["id"]),
        "/items",
        "GET",
        "list_items",
        "List items.",
        int(dataset["id"]),
        ["id", "name"],
    )
    service.create_endpoint(
        conn,
        int(server["id"]),
        "/items/{id}",
        "GET",
        "get_item",
        "Get an item.",
        int(dataset["id"]),
    )
    return {"server": server, "dataset": dataset}


def _cohort_by_slug(client: TestClient) -> dict[str, dict[str, Any]]:
    response = client.get("/api/cohort")
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, list)
    return {server["slug"]: server for server in body}


def test_cohort_lists_seeded_servers_ordered_with_counts_and_urls(
    conn: sqlite3.Connection,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://bootcamp.example/")
    assert seed.seed_if_empty(conn) is True

    response = client.get("/api/cohort")

    assert response.status_code == 200, response.text
    cohort = response.json()
    assert isinstance(cohort, list)
    assert [server["slug"] for server in cohort] == sorted(server["slug"] for server in cohort)
    hris = next(server for server in cohort if server["slug"] == "northwind-hris")
    assert hris == {
        "id": hris["id"],
        "slug": "northwind-hris",
        "name": "Northwind HRIS",
        "description": hris["description"],
        "auth_mode": "none",
        "mcp_url": "https://bootcamp.example/mcp/northwind-hris",
        "rest_url": "https://bootcamp.example/mock/northwind-hris",
        "swagger_url": f"https://bootcamp.example/api/servers/{hris['id']}/swagger",
        "dataset_count": 3,
        "endpoint_count": 9,
        "row_count": 64,
        "seed_count": 64,
        "seeded": True,
        "in_sync": True,
        "call_count": 0,
        "last_call_at": None,
    }


def test_cohort_reports_unseeded_and_out_of_sync_states(
    conn: sqlite3.Connection, client: TestClient
) -> None:
    server = service.create_server(conn, "empty-seed", "Empty Seed", "", "none")
    dataset = service.create_dataset(conn, int(server["id"]), "items", "id")
    service.add_rows(conn, int(dataset["id"]), [{"id": 1, "name": "Live only"}])

    cohort = _cohort_by_slug(client)

    assert cohort["empty-seed"]["dataset_count"] == 1
    assert cohort["empty-seed"]["row_count"] == 1
    assert cohort["empty-seed"]["seed_count"] == 0
    assert cohort["empty-seed"]["seeded"] is False
    assert cohort["empty-seed"]["in_sync"] is False


def test_cohort_in_sync_false_after_mutation_and_true_after_reset(
    conn: sqlite3.Connection, client: TestClient
) -> None:
    created = _create_server(conn, "sync-team")
    service.add_rows(conn, int(created["dataset"]["id"]), [{"id": 2, "name": "Extra"}])

    before = _cohort_by_slug(client)["sync-team"]
    reset = client.post("/api/servers/reset-all-to-seed", json={"prefix": "sync-"})
    after = _cohort_by_slug(client)["sync-team"]

    assert before["row_count"] == 2
    assert before["seed_count"] == 1
    assert before["in_sync"] is False
    assert reset.status_code == 200, reset.text
    assert after["row_count"] == 1
    assert after["seed_count"] == 1
    assert after["in_sync"] is True


def test_cohort_urls_fall_back_to_localhost(
    conn: sqlite3.Connection,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    _create_server(conn, "local-url")

    server = _cohort_by_slug(client)["local-url"]

    assert server["mcp_url"] == "http://localhost:2009/mcp/local-url"
    assert server["rest_url"] == "http://localhost:2009/mock/local-url"
    assert server["swagger_url"] == f"http://localhost:2009/api/servers/{server['id']}/swagger"


def test_traffic_summary_returns_empty_list_on_empty_log(client: TestClient) -> None:
    response = client.get("/api/traffic/summary")

    assert response.status_code == 200, response.text
    assert response.json() == []


def test_traffic_summary_aggregates_orders_and_keeps_null_group_last(
    conn: sqlite3.Connection, client: TestClient
) -> None:
    alpha = _create_server(conn, "alpha")
    beta = _create_server(conn, "beta")
    store.log_call(conn, "manual", "ok", target_slug=None, tool_name=None, duration_ms=1)
    store.log_call(conn, "manual", "bad", target_slug=None, tool_name=None, duration_ms=3)
    store.log_call(conn, "manual", "bad", target_slug=None, tool_name=None, duration_ms=5)

    assert client.get("/mock/alpha/items").status_code == 200
    assert client.get("/mock/alpha/items/999").status_code == 404
    assert client.get("/mock/beta/items").status_code == 200
    assert client.get("/mock/beta/items/1").status_code == 200

    response = client.get("/api/traffic/summary")

    assert response.status_code == 200, response.text
    summary = response.json()
    alpha_summary = next(row for row in summary if row["target_slug"] == "alpha")
    beta_summary = next(row for row in summary if row["target_slug"] == "beta")
    null_summary = summary[-1]
    assert [row["target_slug"] for row in summary[:2]] == ["alpha", "beta"]
    assert alpha_summary["call_count"] == 2
    assert alpha_summary["ok_count"] == 1
    assert alpha_summary["error_count"] == 1
    assert beta_summary["call_count"] == 2
    assert beta_summary["ok_count"] == 2
    assert beta_summary["error_count"] == 0
    assert null_summary["target_slug"] is None
    assert null_summary["call_count"] == 3
    assert null_summary["ok_count"] == 1
    assert null_summary["error_count"] == 2
    assert null_summary["avg_duration_ms"] == 3
    assert alpha["server"]["slug"] == "alpha"
    assert beta["server"]["slug"] == "beta"


def test_bulk_reset_prefix_touches_only_matching_servers(
    conn: sqlite3.Connection, client: TestClient
) -> None:
    team = _create_server(conn, "team-alpha")
    other = _create_server(conn, "other-alpha")
    service.add_rows(conn, int(team["dataset"]["id"]), [{"id": 2, "name": "Team extra"}])
    service.add_rows(conn, int(other["dataset"]["id"]), [{"id": 2, "name": "Other extra"}])

    response = client.post("/api/servers/reset-all-to-seed", json={"prefix": "team-"})
    cohort = _cohort_by_slug(client)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "reset": [
            {"server_id": team["server"]["id"], "slug": "team-alpha", "datasets": 1, "rows": 1}
        ],
        "server_count": 1,
        "dataset_count": 1,
        "row_count": 1,
    }
    assert cohort["team-alpha"]["row_count"] == 1
    assert cohort["team-alpha"]["in_sync"] is True
    assert cohort["other-alpha"]["row_count"] == 2
    assert cohort["other-alpha"]["in_sync"] is False


def test_bulk_reset_without_prefix_touches_everything(
    conn: sqlite3.Connection, client: TestClient
) -> None:
    one = _create_server(conn, "one")
    two = _create_server(conn, "two")
    service.add_rows(conn, int(one["dataset"]["id"]), [{"id": 2}])
    service.add_rows(conn, int(two["dataset"]["id"]), [{"id": 2}])

    response = client.post("/api/servers/reset-all-to-seed")
    cohort = _cohort_by_slug(client)

    assert response.status_code == 200, response.text
    assert response.json()["server_count"] == 2
    assert response.json()["dataset_count"] == 2
    assert response.json()["row_count"] == 2
    assert cohort["one"]["in_sync"] is True
    assert cohort["two"]["in_sync"] is True


def test_bulk_reset_non_matching_prefix_returns_empty_200(client: TestClient) -> None:
    response = client.post("/api/servers/reset-all-to-seed", json={"prefix": "missing"})

    assert response.status_code == 200, response.text
    assert response.json() == {"reset": [], "server_count": 0, "dataset_count": 0, "row_count": 0}


def test_bulk_reset_treats_percent_and_underscore_prefixes_literally(
    conn: sqlite3.Connection, client: TestClient
) -> None:
    _create_server(conn, "hr-team")
    _create_server(conn, "hrx")

    percent = client.post("/api/servers/reset-all-to-seed", json={"prefix": "hr%"})
    underscore = client.post("/api/servers/reset-all-to-seed", json={"prefix": "hr_"})

    assert percent.status_code == 200, percent.text
    assert percent.json() == {"reset": [], "server_count": 0, "dataset_count": 0, "row_count": 0}
    assert underscore.status_code == 200, underscore.text
    assert underscore.json() == {"reset": [], "server_count": 0, "dataset_count": 0, "row_count": 0}
