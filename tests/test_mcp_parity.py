import asyncio
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

import admin_mcp
import db
import main

ROUTE_TO_TOOL: dict[str, str] = {
    "DELETE /api/datasets/{dataset_id}": "delete_dataset",
    "DELETE /api/endpoints/{endpoint_id}": "delete_endpoint",
    "DELETE /api/keys/{key_id}": "delete_api_key",
    "DELETE /api/llm-endpoints/{llm_id}": "delete_llm_endpoint",
    "DELETE /api/recipes/{recipe_id}": "delete_recipe",
    "DELETE /api/relationships/{relationship_id}": "delete_relationship",
    "DELETE /api/servers/{server_id}": "delete_server",
    "DELETE /api/traffic": "clear_traffic",
    "GET /api/catalog": "get_catalog",
    "GET /api/cohort": "get_cohort",
    "GET /api/datasets/{dataset_id}": "get_dataset",
    "GET /api/datasets/{dataset_id}/expands": "list_expands",
    "GET /api/datasets/{dataset_id}/rows": "list_rows",
    "GET /api/endpoints/{endpoint_id}": "get_endpoint",
    "GET /api/keys": "list_api_keys",
    "GET /api/llm-endpoints": "list_llm_endpoints",
    "GET /api/llm-endpoints/{llm_id}": "get_llm_endpoint",
    "GET /api/recipes": "list_recipes",
    "GET /api/recipes/departments": "list_recipe_departments",
    "GET /api/recipes/validate": "validate_recipes",
    "GET /api/recipes/{recipe_id}": "get_recipe",
    "GET /api/recipes/{recipe_id}/handout": "get_recipe_handout",
    "GET /api/servers": "list_servers",
    "GET /api/servers/{server_id}": "get_server",
    "GET /api/servers/{server_id}/datasets": "list_datasets",
    "GET /api/servers/{server_id}/endpoints": "list_endpoints",
    "GET /api/servers/{server_id}/export": "export_server",
    "GET /api/servers/{server_id}/relationships": "list_relationships",
    "GET /api/servers/{server_id}/relationships/validate": "validate_relationships",
    "GET /api/servers/{server_id}/swagger": "export_swagger",
    "GET /api/traffic": "get_traffic",
    "GET /api/traffic/summary": "get_traffic_summary",
    "PATCH /api/datasets/{dataset_id}": "update_dataset",
    "PATCH /api/endpoints/{endpoint_id}": "update_endpoint",
    "PATCH /api/llm-endpoints/{llm_id}": "update_llm_endpoint",
    "PATCH /api/recipes/{recipe_id}": "update_recipe",
    "PATCH /api/servers/{server_id}": "update_server",
    "POST /api/datasets/{dataset_id}/reset-to-seed": "reset_to_seed",
    "POST /api/datasets/{dataset_id}/rows": "add_rows",
    "POST /api/datasets/{dataset_id}/save-as-seed": "save_as_seed",
    "POST /api/keys": "create_api_key",
    "POST /api/llm-endpoints": "create_llm_endpoint",
    "POST /api/recipes": "create_recipe",
    "POST /api/relationships/ensure-demo": "ensure_demo_relationships",
    "POST /api/servers": "create_server",
    "POST /api/servers/import": "import_server",
    "POST /api/servers/reset-all-to-seed": "reset_all_to_seed",
    "POST /api/servers/{server_id}/bulk-clone": "bulk_clone_server",
    "POST /api/servers/{server_id}/clone": "clone_server",
    "POST /api/servers/{server_id}/datasets": "create_dataset",
    "POST /api/servers/{server_id}/endpoints": "create_endpoint",
    "POST /api/servers/{server_id}/relationships": "create_relationship",
    "POST /api/servers/{slug}/tools/{tool_name}/call": "call_tool",
    "PUT /api/datasets/{dataset_id}/rows": "replace_rows",
    "PUT /api/llm-endpoints/{llm_id}/responses": "set_llm_responses",
    "PUT /api/recipes/{recipe_id}/tools": "set_recipe_tools",
}

# MCP has its own auth surface; browser session cookies are meaningless there.
INTENTIONALLY_NOT_IN_MCP: set[str] = {
    "GET /api/auth/me",
    "POST /api/auth/login",
    "POST /api/auth/logout",
}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return db.init_db(str(tmp_path / "mcp-parity.db"))


def _iter_routes(routes: Iterable[Any]) -> Iterable[str]:
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            yield from _iter_routes(original_router.routes)
            continue

        path = getattr(route, "path", "")
        if not path.startswith("/api/") or path.startswith("/api/public/"):
            continue

        methods = (getattr(route, "methods", set()) or set()) - {"HEAD", "OPTIONS"}
        for method in methods:
            yield f"{method} {path}"


def _admin_routes() -> set[str]:
    return set(_iter_routes(main.app.routes))


def _tool_names(conn: sqlite3.Connection) -> set[str]:
    server = admin_mcp.build_admin_server(conn)
    tools = asyncio.run(server.list_tools())
    return {tool.name for tool in tools}


def test_every_admin_api_route_is_covered_or_intentionally_excluded() -> None:
    uncovered = _admin_routes() - set(ROUTE_TO_TOOL) - INTENTIONALLY_NOT_IN_MCP

    assert not uncovered, (
        "Admin API routes without MCP parity: "
        f"{sorted(uncovered)}. Add equivalent tools to admin_mcp.py and ROUTE_TO_TOOL, "
        "or justify true browser-only behavior in INTENTIONALLY_NOT_IN_MCP."
    )


def test_mapped_mcp_tools_exist(conn: sqlite3.Connection) -> None:
    missing_tools = set(ROUTE_TO_TOOL.values()) - _tool_names(conn)

    assert not missing_tools, (
        "ROUTE_TO_TOOL references MCP tools that are not registered by admin_mcp.py: "
        f"{sorted(missing_tools)}. Add the tools or correct the route-to-tool mapping."
    )


def test_route_coverage_map_has_no_stale_routes() -> None:
    stale_routes = set(ROUTE_TO_TOOL) - _admin_routes()

    assert not stale_routes, (
        "ROUTE_TO_TOOL contains routes that no longer exist in the FastAPI app: "
        f"{sorted(stale_routes)}. Remove or update the stale mapping entries."
    )


def test_intentional_exclusions_are_only_browser_session_auth_routes() -> None:
    expected_exclusions = {
        "GET /api/auth/me",
        "POST /api/auth/login",
        "POST /api/auth/logout",
    }

    assert INTENTIONALLY_NOT_IN_MCP == expected_exclusions
