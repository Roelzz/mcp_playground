import asyncio
import inspect
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

import db
import mcp_builder
import seed
import service


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    conn = db.init_db(str(tmp_path / "test.db"))
    seed.seed_if_empty(conn)
    return conn


def _tools(conn: sqlite3.Connection) -> dict[str, Any]:
    server = mcp_builder.build_server(conn, "contoso-orders")
    return {tool.name: tool for tool in server._tool_manager._tools.values()}


def test_build_server_exposes_every_endpoint_as_a_tool(conn: sqlite3.Connection) -> None:
    assert set(_tools(conn)) == {
        "list_orders",
        "search_orders",
        "get_order",
        "create_order",
        "update_order",
        "delete_order",
        "list_order_lines",
    }


def test_fastmcp_marks_generated_tools_async(conn: sqlite3.Connection) -> None:
    tool = _tools(conn)["list_orders"]
    assert inspect.iscoroutinefunction(tool.fn)
    assert tool.is_async is True


def test_build_server_rejects_unknown_slug(conn: sqlite3.Connection) -> None:
    with pytest.raises(service.NotFound):
        mcp_builder.build_server(conn, "nope")


def test_get_tool_requires_only_the_id_param(conn: sqlite3.Connection) -> None:
    schema = _tools(conn)["get_order"].parameters
    assert schema["required"] == ["id"]
    assert set(schema["properties"]) == {"id", "expand"}
    assert schema["properties"]["id"]["type"] == "integer"


def test_list_tool_has_optional_filters_and_limit(conn: sqlite3.Connection) -> None:
    schema = _tools(conn)["list_orders"].parameters
    assert schema.get("required", []) == []
    assert "limit" in schema["properties"]
    assert schema["properties"]["limit"]["default"] == 50
    assert schema["properties"]["total"]["type"] == "number"


def test_search_tool_exposes_a_query_param(conn: sqlite3.Connection) -> None:
    schema = _tools(conn)["search_orders"].parameters
    assert "q" in schema["properties"]
    assert schema.get("required", []) == []


def test_update_tool_requires_the_path_param_only(conn: sqlite3.Connection) -> None:
    schema = _tools(conn)["update_order"].parameters
    assert schema["required"] == ["id"]
    assert "customer" in schema["properties"]


def test_create_tool_has_no_required_params(conn: sqlite3.Connection) -> None:
    schema = _tools(conn)["create_order"].parameters
    assert schema.get("required", []) == []
    assert "automatically" in schema["properties"]["id"]["description"]


def _call(conn: sqlite3.Connection, name: str, args: dict[str, Any]) -> Any:
    return asyncio.run(_tools(conn)[name].fn(**args))


@pytest.mark.asyncio
async def test_tool_calls_do_not_block_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "concurrency.db"
    setup_conn = db.init_db(str(db_file))
    try:
        seed.seed_if_empty(setup_conn)
    finally:
        setup_conn.close()

    conns = [db.connect(str(db_file)), db.connect(str(db_file))]

    def slow_call_tool(
        _conn: sqlite3.Connection,
        _slug: str,
        tool_name: str,
        _params: dict[str, Any],
    ) -> dict[str, str]:
        time.sleep(0.4)
        return {"tool": tool_name}

    monkeypatch.setattr(service, "call_tool", slow_call_tool)
    try:
        tools = [
            mcp_builder.build_server(test_conn, "contoso-orders")._tool_manager._tools[
                "list_orders"
            ]
            for test_conn in conns
        ]
        started = time.perf_counter()
        results = await asyncio.gather(*(tool.run({"limit": 1}) for tool in tools))
    finally:
        for test_conn in conns:
            test_conn.close()

    assert results == [{"tool": "list_orders"}, {"tool": "list_orders"}]
    assert time.perf_counter() - started < 0.7


def test_get_tool_returns_the_row(conn: sqlite3.Connection) -> None:
    row = _call(conn, "get_order", {"id": 1008})
    assert row["id"] == 1008
    assert "_row_id" not in row


def test_list_tool_applies_summary_fields_and_limit(conn: sqlite3.Connection) -> None:
    rows = _call(conn, "list_orders", {"limit": 3})
    assert len(rows) == 3
    assert set(rows[0]) == {"id", "customer", "status", "total"}


def test_list_tool_filters_by_field(conn: sqlite3.Connection) -> None:
    rows = _call(conn, "list_orders", {"status": "cancelled", "limit": 50})
    assert rows
    assert all(row["status"] == "cancelled" for row in rows)


def test_unset_optional_filters_are_stripped(conn: sqlite3.Connection) -> None:
    unfiltered = _call(conn, "list_orders", {"limit": 50})
    assert len(unfiltered) == 40


def test_search_tool_matches_on_free_text(conn: sqlite3.Connection) -> None:
    rows = _call(conn, "search_orders", {"q": "Fabrikam", "limit": 50})
    assert rows
    assert all("Fabrikam" in row["customer"] for row in rows)


def test_update_tool_persists_the_change(conn: sqlite3.Connection) -> None:
    _call(conn, "update_order", {"id": 1008, "status": "cancelled"})
    row = _call(conn, "get_order", {"id": 1008})
    assert row["status"] == "cancelled"


def test_create_tool_assigns_an_id(conn: sqlite3.Connection) -> None:
    created = _call(conn, "create_order", {"customer": "Northwind", "status": "open"})
    assert isinstance(created["id"], int)
    assert (_call(conn, "get_order", {"id": created["id"]}))["customer"] == "Northwind"


def test_create_tool_does_not_store_unset_params(conn: sqlite3.Connection) -> None:
    created = _call(conn, "create_order", {"customer": "Northwind"})
    assert "region" not in created


def test_delete_tool_removes_the_row(conn: sqlite3.Connection) -> None:
    assert (_call(conn, "delete_order", {"id": 1008}))["deleted"] is True
    with pytest.raises(service.ServiceError):
        _call(conn, "get_order", {"id": 1008})


def test_missing_row_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(service.ServiceError):
        _call(conn, "get_order", {"id": 999999})


def test_tools_reflect_endpoint_changes_without_restart(conn: sqlite3.Connection) -> None:
    server_id = int(
        next(s for s in service.list_servers(conn) if s["slug"] == "contoso-orders")["id"]
    )
    endpoint = next(
        e for e in service.list_endpoints(conn, server_id) if e["tool_name"] == "delete_order"
    )
    service.delete_endpoint(conn, int(endpoint["id"]))
    assert "delete_order" not in _tools(conn)


def test_field_schema_drives_param_types(conn: sqlite3.Connection) -> None:
    schema = _tools(conn)["list_order_lines"].parameters["properties"]
    assert schema["quantity"]["type"] == "integer"
    assert schema["unit_price"]["type"] == "number"
    assert schema["sku"]["type"] == "string"


def test_generated_schema_is_json_serialisable(conn: sqlite3.Connection) -> None:
    for tool in _tools(conn).values():
        json.dumps(tool.parameters)


# ---------------------------------------------------------------------------
# expand tests
# ---------------------------------------------------------------------------


def test_tool_count_unchanged_after_expand(conn: sqlite3.Connection) -> None:
    assert len(_tools(conn)) == 7


def test_read_tools_expose_optional_expand_param(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    for name in ("list_orders", "search_orders", "get_order", "list_order_lines"):
        schema = tools[name].parameters
        props = schema.get("properties", {})
        assert "expand" in props, f"{name} missing expand param"
        required = schema.get("required", [])
        assert "expand" not in required, f"{name} expand should be optional"


def test_write_tools_do_not_expose_expand_param(conn: sqlite3.Connection) -> None:
    tools = _tools(conn)
    for name in ("create_order", "update_order", "delete_order"):
        schema = tools[name].parameters
        props = schema.get("properties", {})
        assert "expand" not in props, f"{name} must not have expand param"


def test_list_with_expand_returns_expanded_key(conn: sqlite3.Connection) -> None:
    rows = _call(conn, "list_order_lines", {"expand": "order", "limit": 50})
    assert rows
    for row in rows:
        assert "order" in row
        assert isinstance(row["order"], dict)
