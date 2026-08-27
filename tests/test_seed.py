import sqlite3
from pathlib import Path
from typing import Any

import pytest

import db
import seed
import service

JSON_SCALAR_TYPES = (str, int, float, bool, type(None))


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return db.init_db(str(tmp_path / "test.db"))


def _server(conn: sqlite3.Connection) -> dict[str, Any]:
    servers = service.list_servers(conn)
    assert len(servers) == 1
    return servers[0]


def _datasets_by_key(conn: sqlite3.Connection, server_id: int) -> dict[str, dict[str, Any]]:
    return {dataset["key"]: dataset for dataset in service.list_datasets(conn, server_id)}


def test_seed_if_empty_creates_one_contoso_server(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True

    server = _server(conn)
    assert server["slug"] == "contoso-orders"


def test_seed_if_empty_is_idempotent(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True
    server = _server(conn)
    first_counts = (
        len(service.list_servers(conn)),
        len(service.list_datasets(conn, int(server["id"]))),
        len(service.list_endpoints(conn, int(server["id"]))),
        len(service.list_llm_endpoints(conn)),
        server["row_count"],
    )

    assert seed.seed_if_empty(conn) is False
    server = _server(conn)
    second_counts = (
        len(service.list_servers(conn)),
        len(service.list_datasets(conn, int(server["id"]))),
        len(service.list_endpoints(conn, int(server["id"]))),
        len(service.list_llm_endpoints(conn)),
        server["row_count"],
    )
    assert second_counts == first_counts


def test_dataset_row_counts_include_live_and_seed_rows(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True
    datasets = _datasets_by_key(conn, int(_server(conn)["id"]))

    orders = service.get_dataset(conn, int(datasets["orders"]["id"]))
    order_lines = service.get_dataset(conn, int(datasets["order_lines"]["id"]))

    assert orders["row_count"] == 40
    assert orders["seed_count"] == 40
    assert order_lines["row_count"] == 120
    assert order_lines["seed_count"] == 120


def test_seeded_endpoints_have_expected_tool_types(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True
    endpoints = service.list_endpoints(conn, int(_server(conn)["id"]))

    assert {endpoint["tool_name"] for endpoint in endpoints} == {
        "list_orders",
        "search_orders",
        "get_order",
        "create_order",
        "update_order",
        "list_order_lines",
        "delete_order",
    }
    assert {endpoint["tool_type"] for endpoint in endpoints} == {
        "list",
        "search",
        "get",
        "create",
        "update",
        "delete",
    }


def test_list_orders_returns_summary_projection(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True

    rows = service.call_tool(conn, "contoso-orders", "list_orders", {})

    assert rows
    assert all(set(row) == {"id", "customer", "status", "total"} for row in rows)


def test_get_order_returns_requested_order(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True

    row = service.call_tool(conn, "contoso-orders", "get_order", {"id": 1001})

    assert row["id"] == 1001
    assert row["customer"] == "Northwind Office Supply"


def test_mock_llm_endpoint_has_scripted_responses(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True

    llms = service.list_llm_endpoints(conn)
    assert [llm["slug"] for llm in llms] == ["demo-llm"]
    llm = service.get_llm_endpoint(conn, int(llms[0]["id"]))

    assert llm["mode"] == "mock"
    assert llm["model_name"] == "gpt-4o-mini"
    assert [response["match_type"] for response in llm["responses"]] == [
        "contains",
        "contains",
        "always",
    ]
    assert [response["ordinal"] for response in llm["responses"]] == [0, 1, 2]


def test_all_seeded_row_values_are_json_scalars(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True
    datasets = _datasets_by_key(conn, int(_server(conn)["id"]))

    for key in ("orders", "order_lines"):
        rows = service.list_rows(conn, int(datasets[key]["id"]))
        for row in rows:
            for field, value in row.items():
                if field == "_row_id":
                    continue
                assert isinstance(value, JSON_SCALAR_TYPES)
