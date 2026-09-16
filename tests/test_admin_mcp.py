import asyncio
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


def _call_tool(tool_map: dict[str, Any], tool_key: str, **kwargs: Any) -> Any:
    return asyncio.run(tool_map[tool_key].fn(**kwargs))


def _assert_confirm_refused(result: dict[str, Any]) -> None:
    assert result["ok"] is False
    assert result["error"] == "set confirm=true to proceed"
    assert result["would_affect"]


def _create_server(tools: dict[str, Any], slug: str = "demo") -> dict[str, Any]:
    return _call_tool(tools, "create_server", slug=slug, name=f"{slug} server")


def _create_dataset(
    tools: dict[str, Any],
    server_id: int,
    key: str = "orders",
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _call_tool(tools, "create_dataset", server_id=server_id, key=key, rows=rows)


def _create_get_endpoint(
    tools: dict[str, Any],
    server_id: int,
    dataset_id: int,
    tool_name: str = "get_order",
) -> dict[str, Any]:
    return _call_tool(
        tools,
        "create_endpoint",
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
    _call_tool(
        tools,
        "add_rows",
        dataset_id=dataset["id"],
        rows=[{"id": 42, "customer": "Northwind"}],
    )
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]))

    row = _call_tool(tools, "call_tool", slug="flagship", tool_name="get_order", params={"id": 42})

    assert row == {"id": 42, "customer": "Northwind"}


def test_get_connection_info_uses_public_base_url(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "connect-me")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1, "name": "A"}])
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://playground.example")

    info = _call_tool(tools, "get_connection_info", slug="connect-me")

    assert info["mcp_url"] == "https://playground.example/mcp/connect-me"
    assert info["tool_count"] == 1
    assert info["tools"][0]["name"] == "get_order"


def test_api_key_tools_create_list_and_never_expose_key_hash(
    conn: sqlite3.Connection,
) -> None:
    tools = _tools(conn)

    created = _call_tool(tools, "create_api_key", label="Agent admin", scope="admin")
    keys = _call_tool(tools, "list_api_keys")

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
    created = _call_tool(tools, "create_api_key", label="Delete me", scope="readonly")

    _assert_confirm_refused(_call_tool(tools, "delete_api_key", key_id=created["id"]))
    assert _call_tool(tools, "list_api_keys")[0]["id"] == created["id"]

    deleted = _call_tool(tools, "delete_api_key", key_id=created["id"], confirm=True)

    assert deleted == {"ok": True, "deleted": True, "key_id": created["id"]}
    assert _call_tool(tools, "list_api_keys") == []


def test_export_swagger_uses_default_base_url(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "swagger")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1, "name": "A"}])
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]))

    swagger = _call_tool(tools, "export_swagger", server_id=server["id"])

    assert swagger["swagger"] == "2.0"
    assert swagger["host"] == "localhost:2009"
    assert swagger["basePath"] == "/mock/swagger"
    assert "/orders/{id}" in swagger["paths"]


def test_export_and_import_server_round_trips_bundle(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "portable")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1, "name": "A"}])
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]))

    bundle = _call_tool(tools, "export_server", server_id=server["id"])
    imported = _call_tool(tools, "import_server", bundle=bundle, slug="portable-copy")

    assert bundle["format"] == "mcp-playground-server"
    assert imported["server"]["slug"] == "portable-copy"
    assert imported["datasets"] == 1
    assert imported["endpoints"] == 1
    imported_server = _call_tool(tools, "get_server", server_id=imported["server"]["id"])
    assert imported_server["slug"] == "portable-copy"


def test_not_found_returns_structured_error(conn: sqlite3.Connection) -> None:
    result = _call_tool(_tools(conn), "get_server", server_id=999)

    assert result["ok"] is False
    assert result["code"] == "not_found"


def test_duplicate_slug_returns_conflict(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    _create_server(tools, "dupe")

    result = _call_tool(tools, "create_server", slug="dupe", name="Duplicate")

    assert result["ok"] is False
    assert result["code"] == "conflict"


def test_destructive_tools_refuse_without_confirm_and_succeed(
    conn: sqlite3.Connection,
) -> None:
    tools = _tools(conn)

    server = _create_server(tools, "replace-rows")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1}])
    _assert_confirm_refused(
        _call_tool(tools, "replace_rows", dataset_id=dataset["id"], rows=[{"id": 2}])
    )
    replaced = _call_tool(
        tools,
        "replace_rows",
        dataset_id=dataset["id"],
        rows=[{"id": 2}],
        confirm=True,
    )
    assert replaced["row_count"] == 1
    assert _call_tool(tools, "list_rows", dataset_id=dataset["id"])[0]["id"] == 2

    server = _create_server(tools, "reset-seed")
    dataset = _create_dataset(tools, int(server["id"]), "items", rows=[{"id": 1}])
    _call_tool(tools, "add_rows", dataset_id=dataset["id"], rows=[{"id": 2}])
    _assert_confirm_refused(_call_tool(tools, "reset_to_seed", dataset_id=dataset["id"]))
    reset = _call_tool(tools, "reset_to_seed", dataset_id=dataset["id"], confirm=True)
    assert reset["row_count"] == 1
    assert _call_tool(tools, "list_rows", dataset_id=dataset["id"])[0]["id"] == 1

    server = _create_server(tools, "save-seed")
    dataset = _create_dataset(tools, int(server["id"]), "items", rows=[{"id": 1}])
    _call_tool(tools, "replace_rows", dataset_id=dataset["id"], rows=[{"id": 3}], confirm=True)
    _assert_confirm_refused(_call_tool(tools, "save_as_seed", dataset_id=dataset["id"]))
    saved = _call_tool(tools, "save_as_seed", dataset_id=dataset["id"], confirm=True)
    assert saved["seed_count"] == 1
    _call_tool(tools, "replace_rows", dataset_id=dataset["id"], rows=[], confirm=True)
    _call_tool(tools, "reset_to_seed", dataset_id=dataset["id"], confirm=True)
    assert _call_tool(tools, "list_rows", dataset_id=dataset["id"])[0]["id"] == 3

    server = _create_server(tools, "delete-endpoint")
    dataset = _create_dataset(tools, int(server["id"]))
    endpoint = _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]))
    _assert_confirm_refused(_call_tool(tools, "delete_endpoint", endpoint_id=endpoint["id"]))
    deleted_endpoint = _call_tool(
        tools, "delete_endpoint", endpoint_id=endpoint["id"], confirm=True
    )
    assert deleted_endpoint["ok"] is True
    assert _call_tool(tools, "get_endpoint", endpoint_id=endpoint["id"])["code"] == "not_found"

    server = _create_server(tools, "delete-dataset")
    dataset = _create_dataset(tools, int(server["id"]))
    _assert_confirm_refused(_call_tool(tools, "delete_dataset", dataset_id=dataset["id"]))
    deleted_dataset = _call_tool(tools, "delete_dataset", dataset_id=dataset["id"], confirm=True)
    assert deleted_dataset["ok"] is True
    assert _call_tool(tools, "get_dataset", dataset_id=dataset["id"])["code"] == "not_found"

    server = _create_server(tools, "delete-server")
    _assert_confirm_refused(_call_tool(tools, "delete_server", server_id=server["id"]))
    deleted_server = _call_tool(tools, "delete_server", server_id=server["id"], confirm=True)
    assert deleted_server["ok"] is True
    assert _call_tool(tools, "get_server", server_id=server["id"])["code"] == "not_found"

    llm = _call_tool(tools, "create_llm_endpoint", slug="delete-llm", name="Delete LLM")
    _assert_confirm_refused(_call_tool(tools, "delete_llm_endpoint", llm_id=llm["id"]))
    deleted_llm = _call_tool(tools, "delete_llm_endpoint", llm_id=llm["id"], confirm=True)
    assert deleted_llm["ok"] is True
    assert _call_tool(tools, "get_llm_endpoint", llm_id=llm["id"])["code"] == "not_found"

    _assert_confirm_refused(_call_tool(tools, "clear_traffic"))
    assert _call_tool(tools, "clear_traffic", confirm=True)["ok"] is True


def test_partial_update_server_does_not_blank_other_fields(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    server = _call_tool(
        tools,
        "create_server",
        slug="partial",
        name="Original",
        description="Keep this",
        auth_mode="api_key",
    )

    updated = _call_tool(tools, "update_server", server_id=server["id"], name="Updated")

    assert updated["name"] == "Updated"
    assert updated["slug"] == "partial"
    assert updated["description"] == "Keep this"
    assert updated["auth_mode"] == "api_key"


def test_recipe_tools_manage_recipe_lifecycle(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "recipes")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1}])
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]), "list_orders")

    recipe = _call_tool(
        tools,
        "create_recipe",
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

    assert _call_tool(tools, "list_recipes")[0]["slug"] == "order-triage"
    recipe_detail = _call_tool(tools, "get_recipe", recipe_id=recipe["id"])
    assert recipe_detail["tools"][0]["tool_name"] == "list_orders"

    updated = _call_tool(tools, "update_recipe", recipe_id=recipe["id"], title="Updated triage")
    assert updated["title"] == "Updated triage"

    _call_tool(tools, "set_recipe_tools", recipe_id=recipe["id"], tools=[])
    assert _call_tool(tools, "get_recipe", recipe_id=recipe["id"])["tools"] == []

    _assert_confirm_refused(_call_tool(tools, "delete_recipe", recipe_id=recipe["id"]))
    deleted = _call_tool(tools, "delete_recipe", recipe_id=recipe["id"], confirm=True)
    assert deleted == {"ok": True, "deleted": True, "recipe_id": recipe["id"]}
    assert _call_tool(tools, "get_recipe", recipe_id=recipe["id"])["code"] == "not_found"


def test_recipe_validation_departments_and_handout_tools(
    conn: sqlite3.Connection,
) -> None:
    tools = _tools(conn)
    server = _create_server(tools, "recipe-report")
    dataset = _create_dataset(tools, int(server["id"]), rows=[{"id": 1}])
    _create_get_endpoint(tools, int(server["id"]), int(dataset["id"]), "list_orders")
    recipe = _call_tool(
        tools,
        "create_recipe",
        slug="sales-handout",
        title="Sales handout",
        department="Sales",
        agent_instructions="Use the order tools.",
        example_prompts=["Show orders"],
        tools=[{"server_id": int(server["id"]), "tool_name": "list_orders"}],
    )

    validation = _call_tool(tools, "validate_recipes")
    departments = _call_tool(tools, "list_recipe_departments")
    handout = _call_tool(tools, "get_recipe_handout", recipe_id=recipe["id"])

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
    relationship = _call_tool(
        tools,
        "create_relationship",
        server_id=server["id"],
        name="line_order",
        source_dataset_id=lines["id"],
        source_field="order_id",
        target_dataset_id=orders["id"],
        target_field="id",
        expand_name="order",
        inverse_expand_name="lines",
    )

    line_expands = _call_tool(tools, "list_expands", dataset_id=lines["id"])
    order_expands = _call_tool(tools, "list_expands", dataset_id=orders["id"])

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
