import inspect
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from mcp.server import FastMCP

import admin_mcp
import db
import service

EXPECTED_TOOL_NAMES = {
    "list_servers",
    "get_server",
    "create_server",
    "clone_server",
    "bulk_clone_server",
    "get_catalog",
    "get_cohort",
    "update_server",
    "delete_server",
    "get_connection_info",
    "list_api_keys",
    "create_api_key",
    "delete_api_key",
    "export_swagger",
    "export_server",
    "import_server",
    "list_datasets",
    "get_dataset",
    "create_dataset",
    "update_dataset",
    "delete_dataset",
    "list_rows",
    "replace_rows",
    "add_rows",
    "reset_to_seed",
    "reset_all_to_seed",
    "save_as_seed",
    "list_endpoints",
    "get_endpoint",
    "create_endpoint",
    "update_endpoint",
    "delete_endpoint",
    "list_llm_endpoints",
    "get_llm_endpoint",
    "create_llm_endpoint",
    "update_llm_endpoint",
    "delete_llm_endpoint",
    "set_llm_responses",
    "list_recipes",
    "get_recipe",
    "create_recipe",
    "update_recipe",
    "delete_recipe",
    "set_recipe_tools",
    "validate_recipes",
    "list_recipe_departments",
    "get_recipe_handout",
    "call_tool",
    "get_traffic",
    "get_traffic_summary",
    "clear_traffic",
    "list_relationships",
    "create_relationship",
    "delete_relationship",
    "validate_relationships",
    "list_expands",
    "ensure_demo_relationships",
}

ALLOWED_GAPS = {
    # Slug lookup is used internally by get_connection_info and call_tool, not exposed separately.
    "get_server_by_slug",
    # Slug lookup is internal plumbing; admins manage LLM endpoints by ID through this MCP surface.
    "get_llm_endpoint_by_slug",
    # Relationship helpers used internally by API and Phase 4; not exposed as separate admin tools.
    "get_relationship",
    "parse_expand",
    "expand_rows",
    # Exposed through LLM-facing admin aliases: list_expands and list_recipe_departments.
    "available_expands",
    "recipe_departments",
    # Recipe helpers that mirror slug lookup and aggregate API helpers.
    "get_recipe_by_slug",
    "recipe_counts_by_server",
}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return db.init_db(str(tmp_path / "test.db"))


def _tools(conn: sqlite3.Connection) -> dict[str, Any]:
    server = admin_mcp.build_admin_server(conn)
    return {tool.name: tool for tool in server._tool_manager._tools.values()}


def _assert_confirm_refused(result: dict[str, Any]) -> None:
    assert result["ok"] is False
    assert result["error"] == "set confirm=true to proceed"
    assert result["would_affect"]


def _create_server(tools: dict[str, Any], slug: str = "demo") -> dict[str, Any]:
    return tools["create_server"].fn(slug=slug, name=f"{slug} server")


def _create_dataset(
    tools: dict[str, Any],
    server_id: int,
    key: str = "orders",
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return tools["create_dataset"].fn(server_id=server_id, key=key, rows=rows)


def _create_get_endpoint(
    tools: dict[str, Any],
    server_id: int,
    dataset_id: int,
    tool_name: str = "get_order",
) -> dict[str, Any]:
    return tools["create_endpoint"].fn(
        server_id=server_id,
        path="/orders/{id}",
        tool_name=tool_name,
        dataset_id=dataset_id,
    )


def test_build_admin_server_registers_expected_tools(conn: sqlite3.Connection) -> None:
    server = admin_mcp.build_admin_server(conn)
    assert isinstance(server, FastMCP)
    assert set(_tools(conn)) == EXPECTED_TOOL_NAMES
    assert len(_tools(conn)) == 57


def test_every_tool_has_description_and_parameters(conn: sqlite3.Connection) -> None:
    for tool in _tools(conn).values():
        assert tool.description
        assert isinstance(tool.parameters, dict)
        assert tool.parameters["type"] == "object"
        json.dumps(tool.parameters)


def test_flagship_flow_creates_server_dataset_endpoint_and_calls_tool(
    conn: sqlite3.Connection,
) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "flagship")
    dataset = _create_dataset(tools, int(server["id"]))
    tools["add_rows"].fn(dataset_id=dataset["id"], rows=[{"id": 42, "customer": "Northwind"}])
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]))

    row = tools["call_tool"].fn(slug="flagship", tool_name="get_order", params={"id": 42})

    assert row == {"id": 42, "customer": "Northwind"}


def test_get_connection_info_uses_public_base_url(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "connect-me")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1, "name": "A"}])
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://playground.example")

    info = tools["get_connection_info"].fn(slug="connect-me")

    assert info["mcp_url"] == "https://playground.example/mcp/connect-me"
    assert info["tool_count"] == 1
    assert info["tools"][0]["name"] == "get_order"


def test_api_key_tools_create_list_and_never_expose_key_hash(
    conn: sqlite3.Connection,
) -> None:
    tools = _tools(conn)

    created = tools["create_api_key"].fn(label="Agent admin", scope="admin")
    keys = tools["list_api_keys"].fn()

    assert created["key"]
    assert created["label"] == "Agent admin"
    assert created["scope"] == "admin"
    assert "key_hash" not in created
    assert "key" not in keys[0]
    assert "key_hash" not in keys[0]
    assert keys[0]["id"] == created["id"]


def test_delete_api_key_requires_confirmation_and_revokes(
    conn: sqlite3.Connection,
) -> None:
    tools = _tools(conn)
    created = tools["create_api_key"].fn(label="Delete me", scope="readonly")

    _assert_confirm_refused(tools["delete_api_key"].fn(key_id=created["id"]))
    assert tools["list_api_keys"].fn()[0]["id"] == created["id"]

    deleted = tools["delete_api_key"].fn(key_id=created["id"], confirm=True)

    assert deleted == {"ok": True, "deleted": True, "key_id": created["id"]}
    assert tools["list_api_keys"].fn() == []


def test_export_swagger_uses_default_base_url(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "swagger")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1, "name": "A"}])
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]))

    swagger = tools["export_swagger"].fn(server_id=server["id"])

    assert swagger["swagger"] == "2.0"
    assert swagger["host"] == "localhost:2009"
    assert swagger["basePath"] == "/mock/swagger"
    assert "/orders/{id}" in swagger["paths"]


def test_export_and_import_server_round_trips_bundle(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "portable")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1, "name": "A"}])
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]))

    bundle = tools["export_server"].fn(server_id=server["id"])
    imported = tools["import_server"].fn(bundle=bundle, slug="portable-copy")

    assert bundle["format"] == "mcp-playground-server"
    assert imported["server"]["slug"] == "portable-copy"
    assert imported["datasets"] == 1
    assert imported["endpoints"] == 1
    assert tools["get_server"].fn(server_id=imported["server"]["id"])["slug"] == "portable-copy"


def test_not_found_returns_structured_error(conn: sqlite3.Connection) -> None:
    result = _tools(conn)["get_server"].fn(server_id=999)

    assert result["ok"] is False
    assert result["code"] == "not_found"


def test_duplicate_slug_returns_conflict(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    _create_server(tools, "dupe")

    result = tools["create_server"].fn(slug="dupe", name="Duplicate")

    assert result["ok"] is False
    assert result["code"] == "conflict"


def test_destructive_tools_refuse_without_confirm_and_succeed(
    conn: sqlite3.Connection,
) -> None:
    tools = _tools(conn)

    server = _create_server(tools, "replace-rows")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1}])
    _assert_confirm_refused(tools["replace_rows"].fn(dataset_id=dataset["id"], rows=[{"id": 2}]))
    replaced = tools["replace_rows"].fn(dataset_id=dataset["id"], rows=[{"id": 2}], confirm=True)
    assert replaced["row_count"] == 1
    assert tools["list_rows"].fn(dataset_id=dataset["id"])[0]["id"] == 2

    server = _create_server(tools, "reset-seed")
    dataset = _create_dataset(tools, int(server["id"]), "items", rows=[{"id": 1}])
    tools["add_rows"].fn(dataset_id=dataset["id"], rows=[{"id": 2}])
    _assert_confirm_refused(tools["reset_to_seed"].fn(dataset_id=dataset["id"]))
    reset = tools["reset_to_seed"].fn(dataset_id=dataset["id"], confirm=True)
    assert reset["row_count"] == 1
    assert tools["list_rows"].fn(dataset_id=dataset["id"])[0]["id"] == 1

    server = _create_server(tools, "save-seed")
    dataset = _create_dataset(tools, int(server["id"]), "items", rows=[{"id": 1}])
    tools["replace_rows"].fn(dataset_id=dataset["id"], rows=[{"id": 3}], confirm=True)
    _assert_confirm_refused(tools["save_as_seed"].fn(dataset_id=dataset["id"]))
    saved = tools["save_as_seed"].fn(dataset_id=dataset["id"], confirm=True)
    assert saved["seed_count"] == 1
    tools["replace_rows"].fn(dataset_id=dataset["id"], rows=[], confirm=True)
    tools["reset_to_seed"].fn(dataset_id=dataset["id"], confirm=True)
    assert tools["list_rows"].fn(dataset_id=dataset["id"])[0]["id"] == 3

    server = _create_server(tools, "delete-endpoint")
    dataset = _create_dataset(tools, int(server["id"]))
    endpoint = _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]))
    _assert_confirm_refused(tools["delete_endpoint"].fn(endpoint_id=endpoint["id"]))
    assert tools["delete_endpoint"].fn(endpoint_id=endpoint["id"], confirm=True)["ok"] is True
    assert tools["get_endpoint"].fn(endpoint_id=endpoint["id"])["code"] == "not_found"

    server = _create_server(tools, "delete-dataset")
    dataset = _create_dataset(tools, int(server["id"]))
    _assert_confirm_refused(tools["delete_dataset"].fn(dataset_id=dataset["id"]))
    assert tools["delete_dataset"].fn(dataset_id=dataset["id"], confirm=True)["ok"] is True
    assert tools["get_dataset"].fn(dataset_id=dataset["id"])["code"] == "not_found"

    server = _create_server(tools, "delete-server")
    _assert_confirm_refused(tools["delete_server"].fn(server_id=server["id"]))
    assert tools["delete_server"].fn(server_id=server["id"], confirm=True)["ok"] is True
    assert tools["get_server"].fn(server_id=server["id"])["code"] == "not_found"

    llm = tools["create_llm_endpoint"].fn(slug="delete-llm", name="Delete LLM")
    _assert_confirm_refused(tools["delete_llm_endpoint"].fn(llm_id=llm["id"]))
    assert tools["delete_llm_endpoint"].fn(llm_id=llm["id"], confirm=True)["ok"] is True
    assert tools["get_llm_endpoint"].fn(llm_id=llm["id"])["code"] == "not_found"

    _assert_confirm_refused(tools["clear_traffic"].fn())
    assert tools["clear_traffic"].fn(confirm=True)["ok"] is True


def test_partial_update_server_does_not_blank_other_fields(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    server = tools["create_server"].fn(
        slug="partial",
        name="Original",
        description="Keep this",
        auth_mode="api_key",
    )

    updated = tools["update_server"].fn(server_id=server["id"], name="Updated")

    assert updated["name"] == "Updated"
    assert updated["slug"] == "partial"
    assert updated["description"] == "Keep this"
    assert updated["auth_mode"] == "api_key"


def test_recipe_tools_manage_recipe_lifecycle(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "recipes")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1}])
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]), "list_orders")

    recipe = tools["create_recipe"].fn(
        slug="order-triage",
        title="Order triage",
        department="Operations",
        skill="beginner",
        agent_instructions="Use the orders MCP tools.",
        example_prompts=["Find delayed orders"],
        destinations=["Copilot Studio"],
        published=True,
        tools=[{"server_id": int(server["id"]), "tool_name": "list_orders"}],
    )

    assert tools["list_recipes"].fn()[0]["slug"] == "order-triage"
    assert tools["get_recipe"].fn(recipe_id=recipe["id"])["tools"][0]["tool_name"] == "list_orders"

    updated = tools["update_recipe"].fn(recipe_id=recipe["id"], title="Updated triage")
    assert updated["title"] == "Updated triage"

    tools["set_recipe_tools"].fn(recipe_id=recipe["id"], tools=[])
    assert tools["get_recipe"].fn(recipe_id=recipe["id"])["tools"] == []

    _assert_confirm_refused(tools["delete_recipe"].fn(recipe_id=recipe["id"]))
    deleted = tools["delete_recipe"].fn(recipe_id=recipe["id"], confirm=True)
    assert deleted == {"ok": True, "deleted": True, "recipe_id": recipe["id"]}
    assert tools["get_recipe"].fn(recipe_id=recipe["id"])["code"] == "not_found"


def test_recipe_validation_departments_and_handout_tools(
    conn: sqlite3.Connection,
) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "recipe-report")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1}])
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]), "list_orders")
    recipe = tools["create_recipe"].fn(
        slug="sales-handout",
        title="Sales handout",
        department="Sales",
        agent_instructions="Use the order tools.",
        example_prompts=["Show orders"],
        tools=[{"server_id": int(server["id"]), "tool_name": "list_orders"}],
    )

    validation = tools["validate_recipes"].fn()
    departments = tools["list_recipe_departments"].fn()
    handout = tools["get_recipe_handout"].fn(recipe_id=recipe["id"])

    assert validation["ok"] is True
    assert validation["recipe_count"] == 1
    assert departments == [{"department": "Sales", "count": 1}]
    assert handout["slug"] == "sales-handout"
    assert "# Sales handout" in handout["markdown"]
    assert "`list_orders`" in handout["markdown"]


def test_list_expands_returns_dataset_relationship_options(
    conn: sqlite3.Connection,
) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "expands")
    orders = _create_dataset(tools, int(server["id"]), "orders", rows=[{"id": 1}])
    lines = _create_dataset(
        tools,
        int(server["id"]),
        "lines",
        rows=[{"id": 10, "order_id": 1}],
    )
    relationship = tools["create_relationship"].fn(
        server_id=server["id"],
        name="line_order",
        source_dataset_id=lines["id"],
        source_field="order_id",
        target_dataset_id=orders["id"],
        target_field="id",
        expand_name="order",
        inverse_expand_name="lines",
    )

    line_expands = tools["list_expands"].fn(dataset_id=lines["id"])
    order_expands = tools["list_expands"].fn(dataset_id=orders["id"])

    assert line_expands[0]["relationship_id"] == relationship["id"]
    assert line_expands[0]["name"] == "order"
    assert line_expands[0]["direction"] == "forward"
    assert order_expands[0]["name"] == "lines"
    assert order_expands[0]["direction"] == "inverse"


def test_service_functions_have_admin_tools() -> None:
    service_functions = {
        name
        for name, fn in inspect.getmembers(service, inspect.isfunction)
        if not name.startswith("_") and fn.__module__ == "service"
    }

    assert ALLOWED_GAPS <= service_functions
    assert service_functions - ALLOWED_GAPS <= EXPECTED_TOOL_NAMES
