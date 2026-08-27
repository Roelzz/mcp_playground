import sqlite3
from pathlib import Path
from typing import Any

import pytest

import db
import seed
import service

JSON_SCALAR_TYPES = (str, int, float, bool, type(None))
EXPECTED_DATASETS = {
    "contoso-orders": {"orders": 40, "order_lines": 120},
    "northwind-hris": {"employees": 30, "time_off_requests": 28, "org_units": 6},
    "fabrikam-it-service": {"tickets": 30, "assets": 18, "service_catalog": 8},
    "adatum-crm": {"accounts": 20, "contacts": 30, "opportunities": 26},
    "contoso-expenses": {
        "expense_reports": 24,
        "expense_lines": 60,
        "cost_centres": 8,
        "approvals": 24,
    },
}
EXPECTED_SERVER_SLUGS = set(EXPECTED_DATASETS)
NEW_SERVER_SLUGS = EXPECTED_SERVER_SLUGS - {"contoso-orders"}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return db.init_db(str(tmp_path / "test.db"))


def _servers_by_slug(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {server["slug"]: server for server in service.list_servers(conn)}


def _server(conn: sqlite3.Connection, slug: str = "contoso-orders") -> dict[str, Any]:
    servers = _servers_by_slug(conn)
    assert slug in servers
    return servers[slug]


def _datasets_by_key(conn: sqlite3.Connection, server_id: int) -> dict[str, dict[str, Any]]:
    return {dataset["key"]: dataset for dataset in service.list_datasets(conn, server_id)}


def _rows_by_dataset_key(conn: sqlite3.Connection, slug: str) -> dict[str, list[dict[str, Any]]]:
    datasets = _datasets_by_key(conn, int(_server(conn, slug)["id"]))
    return {key: service.list_rows(conn, int(dataset["id"])) for key, dataset in datasets.items()}


def _seed_counts(conn: sqlite3.Connection) -> tuple[int, int, int, int, int]:
    servers = service.list_servers(conn)
    return (
        len(servers),
        sum(len(service.list_datasets(conn, int(server["id"]))) for server in servers),
        sum(len(service.list_endpoints(conn, int(server["id"]))) for server in servers),
        len(service.list_llm_endpoints(conn)),
        sum(int(server["row_count"]) for server in servers),
    )


def test_seed_if_empty_creates_expected_demo_servers(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True

    assert set(_servers_by_slug(conn)) == EXPECTED_SERVER_SLUGS


def test_seed_if_empty_is_idempotent(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True
    first_counts = _seed_counts(conn)

    assert seed.seed_if_empty(conn) is False
    second_counts = _seed_counts(conn)
    assert second_counts == first_counts


def test_dataset_row_counts_include_live_and_seed_rows(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True

    for slug, expected in EXPECTED_DATASETS.items():
        datasets = _datasets_by_key(conn, int(_server(conn, slug)["id"]))
        assert set(datasets) == set(expected)
        for key, count in expected.items():
            dataset = service.get_dataset(conn, int(datasets[key]["id"]))
            assert dataset["row_count"] == count
            assert dataset["seed_count"] == count


def test_seeded_endpoints_have_expected_tool_types(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True
    expected = {
        "contoso-orders": {
            "list_orders": "list",
            "search_orders": "search",
            "get_order": "get",
            "create_order": "create",
            "update_order": "update",
            "list_order_lines": "list",
            "delete_order": "delete",
        },
        "northwind-hris": {
            "list_employees": "list",
            "search_employees": "search",
            "get_employee": "get",
            "list_time_off_requests": "list",
            "search_time_off_requests": "search",
            "get_time_off_request": "get",
            "create_time_off_request": "create",
            "update_time_off_request": "update",
            "list_org_units": "list",
        },
        "fabrikam-it-service": {
            "list_tickets": "list",
            "search_tickets": "search",
            "get_ticket": "get",
            "update_ticket": "update",
            "list_assets": "list",
            "search_assets": "search",
            "get_asset": "get",
            "list_service_catalog": "list",
        },
        "adatum-crm": {
            "list_accounts": "list",
            "search_accounts": "search",
            "get_account": "get",
            "list_contacts": "list",
            "search_contacts": "search",
            "list_opportunities": "list",
            "search_opportunities": "search",
            "get_opportunity": "get",
            "update_opportunity": "update",
        },
        "contoso-expenses": {
            "list_expense_reports": "list",
            "search_expense_reports": "search",
            "get_expense_report": "get",
            "update_expense_report": "update",
            "list_expense_lines": "list",
            "search_expense_lines": "search",
            "list_cost_centres": "list",
            "get_cost_centre": "get",
            "list_approvals": "list",
        },
    }

    for slug, expected_tools in expected.items():
        endpoints = service.list_endpoints(conn, int(_server(conn, slug)["id"]))
        assert {
            endpoint["tool_name"]: endpoint["tool_type"] for endpoint in endpoints
        } == expected_tools


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


def test_new_seeded_servers_have_referential_integrity(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True

    hris = _rows_by_dataset_key(conn, "northwind-hris")
    employee_ids = {row["employee_id"] for row in hris["employees"]}
    org_unit_ids = {row["org_unit_id"] for row in hris["org_units"]}
    assert all(
        row["manager_id"] == "" or row["manager_id"] in employee_ids for row in hris["employees"]
    )
    assert all(row["employee_id"] in employee_ids for row in hris["time_off_requests"])
    assert all(row["approver_id"] in employee_ids for row in hris["time_off_requests"])
    assert all(row["lead_employee_id"] in employee_ids for row in hris["org_units"])
    assert all(
        row["parent_org_unit_id"] == "" or row["parent_org_unit_id"] in org_unit_ids
        for row in hris["org_units"]
    )

    itsm = _rows_by_dataset_key(conn, "fabrikam-it-service")
    asset_tags = {row["asset_tag"] for row in itsm["assets"]}
    service_ids = {row["service_id"] for row in itsm["service_catalog"]}
    assert all(row["service_id"] in service_ids for row in itsm["assets"])
    assert all(row["service_id"] in service_ids for row in itsm["tickets"])
    assert all(row["asset_tag"] == "" or row["asset_tag"] in asset_tags for row in itsm["tickets"])

    crm = _rows_by_dataset_key(conn, "adatum-crm")
    account_ids = {row["account_id"] for row in crm["accounts"]}
    contact_accounts = {row["contact_id"]: row["account_id"] for row in crm["contacts"]}
    assert all(row["account_id"] in account_ids for row in crm["contacts"])
    assert all(row["account_id"] in account_ids for row in crm["opportunities"])
    assert all(row["primary_contact_id"] in contact_accounts for row in crm["opportunities"])
    assert all(
        contact_accounts[row["primary_contact_id"]] == row["account_id"]
        for row in crm["opportunities"]
    )

    finance = _rows_by_dataset_key(conn, "contoso-expenses")
    report_ids = {row["report_id"] for row in finance["expense_reports"]}
    cost_center_ids = {row["cost_center_id"] for row in finance["cost_centres"]}
    assert all(row["cost_center_id"] in cost_center_ids for row in finance["expense_reports"])
    assert all(row["report_id"] in report_ids for row in finance["expense_lines"])
    assert all(row["cost_center_id"] in cost_center_ids for row in finance["expense_lines"])
    assert all(row["report_id"] in report_ids for row in finance["approvals"])


def test_all_seeded_row_values_are_json_scalars(conn: sqlite3.Connection) -> None:
    assert seed.seed_if_empty(conn) is True

    for slug in EXPECTED_SERVER_SLUGS:
        for rows in _rows_by_dataset_key(conn, slug).values():
            for row in rows:
                for field, value in row.items():
                    if field == "_row_id":
                        continue
                    assert isinstance(value, JSON_SCALAR_TYPES)
