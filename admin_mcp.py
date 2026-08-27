"""Build the management FastMCP server for administering MCP Playground."""

from __future__ import annotations

import inspect
import os
import sqlite3
from collections.abc import Callable
from typing import Annotated, Any

from loguru import logger
from mcp.server import FastMCP
from pydantic import Field

import service

REQUIRED = inspect.Parameter.empty

ParamSpec = tuple[str, Any, str, Any]
Handler = Callable[..., Any]

ADMIN_INSTRUCTIONS = (
    "This MCP server manages MCP Playground itself. Use these tools to create and edit "
    "mock MCP servers, datasets, endpoint tools, OpenAI-compatible LLM endpoints, and "
    "traffic logs for Microsoft Copilot Studio demos."
)

CONFIRM_ERROR = "set confirm=true to proceed"


def _error(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": message, "code": code}


def _call_service(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except service.NotFound as exc:
        return _error("not_found", str(exc))
    except service.Conflict as exc:
        return _error("conflict", str(exc))
    except service.ServiceError as exc:
        return _error(getattr(exc, "code", "error"), str(exc))


def _is_error(value: Any) -> bool:
    return isinstance(value, dict) and value.get("ok") is False and "error" in value


def _supplied(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in kwargs.items() if value is not None}


def _confirm_required(would_affect: str) -> dict[str, Any]:
    return {"ok": False, "error": CONFIRM_ERROR, "would_affect": would_affect}


def _ok(**extra: Any) -> dict[str, Any]:
    return {"ok": True, **extra}


def _param(name: str, py_type: Any, description: str, default: Any = REQUIRED) -> ParamSpec:
    return (name, py_type, description, default)


def _make_tool(
    conn: sqlite3.Connection,
    tool_name: str,
    description: str,
    params: list[ParamSpec],
    handler: Handler,
) -> Callable[..., Any]:
    def impl(**kwargs: Any) -> Any:
        logger.info(f"admin mcp tool invoked: {tool_name}")
        bound = signature.bind(**kwargs)
        bound.apply_defaults()
        return handler(conn, **bound.arguments)

    signature_params = []
    annotations: dict[str, Any] = {}
    for name, py_type, param_desc, default in params:
        annotation = Annotated[py_type, Field(description=param_desc)]
        annotations[name] = annotation
        signature_params.append(
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                annotation=annotation,
                default=default,
            )
        )

    signature = inspect.Signature(signature_params, return_annotation=Any)
    impl.__signature__ = signature  # type: ignore[attr-defined]
    impl.__annotations__ = {**annotations, "return": Any}
    impl.__name__ = tool_name
    impl.__doc__ = description
    return impl


def _list_servers(conn: sqlite3.Connection) -> Any:
    return _call_service(service.list_servers, conn)


def _get_server(conn: sqlite3.Connection, server_id: int) -> Any:
    return _call_service(service.get_server, conn, server_id)


def _create_server(
    conn: sqlite3.Connection,
    slug: str,
    name: str,
    description: str = "",
    auth_mode: str = "none",
) -> Any:
    return _call_service(service.create_server, conn, slug, name, description, auth_mode)


def _clone_server(
    conn: sqlite3.Connection,
    server_id: int,
    slug: str,
    name: str | None = None,
) -> Any:
    return _call_service(service.clone_server, conn, server_id, slug, name)


def _bulk_clone_server(
    conn: sqlite3.Connection,
    server_id: int,
    prefix: str,
    count: int,
    start: int = 1,
) -> Any:
    return _call_service(service.bulk_clone_server, conn, server_id, prefix, count, start)


def _get_catalog(conn: sqlite3.Connection) -> Any:
    return _call_service(service.get_catalog, conn)


def _get_cohort(conn: sqlite3.Connection) -> Any:
    return _call_service(service.get_cohort, conn)


def _update_server(conn: sqlite3.Connection, server_id: int, **kwargs: Any) -> Any:
    return _call_service(service.update_server, conn, server_id, **_supplied(kwargs))


def _delete_server(
    conn: sqlite3.Connection, server_id: int, confirm: bool = False
) -> dict[str, Any]:
    if not confirm:
        return _confirm_required(f"server {server_id} and its datasets/endpoints")
    result = _call_service(service.delete_server, conn, server_id)
    if _is_error(result):
        return result
    return _ok(deleted=True, server_id=server_id)


def _get_connection_info(conn: sqlite3.Connection, slug: str) -> Any:
    server_row = _call_service(service.get_server_by_slug, conn, slug)
    if _is_error(server_row):
        return server_row

    endpoints = _call_service(service.list_endpoints, conn, int(server_row["id"]))
    if _is_error(endpoints):
        return endpoints

    base_url = (os.getenv("PUBLIC_BASE_URL") or "http://localhost:2009").rstrip("/")
    auth_mode = str(server_row["auth_mode"])
    return {
        "server_name": server_row["name"],
        "slug": slug,
        "mcp_url": f"{base_url}/mcp/{slug}",
        "auth_mode": auth_mode,
        "transport": "Streamable HTTP",
        "tool_count": len(endpoints),
        "tools": [
            {
                "name": endpoint["tool_name"],
                "tool_type": endpoint["tool_type"],
                "description": endpoint.get("description") or "",
            }
            for endpoint in endpoints
        ],
        "instructions": [
            "Open the Copilot Studio agent and go to Tools.",
            "Choose Add a tool, then select Model Context Protocol.",
            "Paste the mcp_url value as the server URL.",
            "Select Streamable HTTP as the transport.",
            "Configure authentication to match auth_mode.",
            "Save the tool connection, then test one of the listed tools.",
        ],
        "notes": [
            "Generative orchestration must be ON for MCP tools to be invoked.",
            "Use the exact mcp_url; the slug selects which playground server is exposed.",
            "If auth_mode is api_key, the host app must provide the required API key.",
        ],
    }


def _list_datasets(conn: sqlite3.Connection, server_id: int) -> Any:
    return _call_service(service.list_datasets, conn, server_id)


def _get_dataset(conn: sqlite3.Connection, dataset_id: int) -> Any:
    return _call_service(service.get_dataset, conn, dataset_id)


def _create_dataset(
    conn: sqlite3.Connection,
    server_id: int,
    key: str,
    id_field: str = "id",
    rows: list[dict[str, Any]] | None = None,
) -> Any:
    return _call_service(service.create_dataset, conn, server_id, key, id_field, rows)


def _update_dataset(conn: sqlite3.Connection, dataset_id: int, **kwargs: Any) -> Any:
    return _call_service(service.update_dataset, conn, dataset_id, **_supplied(kwargs))


def _delete_dataset(
    conn: sqlite3.Connection, dataset_id: int, confirm: bool = False
) -> dict[str, Any]:
    if not confirm:
        return _confirm_required(f"dataset {dataset_id}")
    result = _call_service(service.delete_dataset, conn, dataset_id)
    if _is_error(result):
        return result
    return _ok(deleted=True, dataset_id=dataset_id)


def _list_rows(conn: sqlite3.Connection, dataset_id: int) -> Any:
    return _call_service(service.list_rows, conn, dataset_id)


def _replace_rows(
    conn: sqlite3.Connection,
    dataset_id: int,
    rows: list[dict[str, Any]],
    confirm: bool = False,
) -> Any:
    if not confirm:
        return _confirm_required(f"all live rows in dataset {dataset_id}")
    return _call_service(service.replace_rows, conn, dataset_id, rows)


def _add_rows(conn: sqlite3.Connection, dataset_id: int, rows: list[dict[str, Any]]) -> Any:
    return _call_service(service.add_rows, conn, dataset_id, rows)


def _reset_to_seed(conn: sqlite3.Connection, dataset_id: int, confirm: bool = False) -> Any:
    if not confirm:
        return _confirm_required(f"live rows in dataset {dataset_id}")
    return _call_service(service.reset_to_seed, conn, dataset_id)


def _reset_all_to_seed(
    conn: sqlite3.Connection, prefix: str | None = None, confirm: bool = False
) -> Any:
    would_affect = f"servers with slug prefix {prefix!r}" if prefix is not None else "all servers"
    if not confirm:
        return _confirm_required(would_affect)
    return _call_service(service.reset_all_to_seed, conn, prefix)


def _save_as_seed(conn: sqlite3.Connection, dataset_id: int, confirm: bool = False) -> Any:
    if not confirm:
        return _confirm_required(f"seed snapshot for dataset {dataset_id}")
    return _call_service(service.save_as_seed, conn, dataset_id)


def _list_endpoints(conn: sqlite3.Connection, server_id: int) -> Any:
    return _call_service(service.list_endpoints, conn, server_id)


def _get_endpoint(conn: sqlite3.Connection, endpoint_id: int) -> Any:
    return _call_service(service.get_endpoint, conn, endpoint_id)


def _create_endpoint(
    conn: sqlite3.Connection,
    server_id: int,
    path: str,
    method: str = "GET",
    tool_name: str = "",
    description: str = "",
    dataset_id: int | None = None,
    summary_fields: list[str] | None = None,
) -> Any:
    return _call_service(
        service.create_endpoint,
        conn,
        server_id,
        path,
        method,
        tool_name,
        description,
        dataset_id,
        summary_fields,
    )


def _update_endpoint(conn: sqlite3.Connection, endpoint_id: int, **kwargs: Any) -> Any:
    return _call_service(service.update_endpoint, conn, endpoint_id, **_supplied(kwargs))


def _delete_endpoint(
    conn: sqlite3.Connection, endpoint_id: int, confirm: bool = False
) -> dict[str, Any]:
    if not confirm:
        return _confirm_required(f"endpoint {endpoint_id}")
    result = _call_service(service.delete_endpoint, conn, endpoint_id)
    if _is_error(result):
        return result
    return _ok(deleted=True, endpoint_id=endpoint_id)


def _list_llm_endpoints(conn: sqlite3.Connection) -> Any:
    return _call_service(service.list_llm_endpoints, conn)


def _get_llm_endpoint(conn: sqlite3.Connection, llm_id: int) -> Any:
    return _call_service(service.get_llm_endpoint, conn, llm_id)


def _create_llm_endpoint(
    conn: sqlite3.Connection,
    slug: str,
    name: str,
    description: str = "",
    mode: str = "mock",
    model_name: str = "playground-model",
    upstream_url: str | None = None,
    upstream_key: str | None = None,
    upstream_deployment: str | None = None,
    system_prompt: str | None = None,
    auth_mode: str = "none",
) -> Any:
    fields = _supplied(
        {
            "slug": slug,
            "name": name,
            "description": description,
            "mode": mode,
            "model_name": model_name,
            "upstream_url": upstream_url,
            "upstream_key": upstream_key,
            "upstream_deployment": upstream_deployment,
            "system_prompt": system_prompt,
            "auth_mode": auth_mode,
        }
    )
    return _call_service(service.create_llm_endpoint, conn, **fields)


def _update_llm_endpoint(conn: sqlite3.Connection, llm_id: int, **kwargs: Any) -> Any:
    return _call_service(service.update_llm_endpoint, conn, llm_id, **_supplied(kwargs))


def _delete_llm_endpoint(
    conn: sqlite3.Connection, llm_id: int, confirm: bool = False
) -> dict[str, Any]:
    if not confirm:
        return _confirm_required(f"LLM endpoint {llm_id}")
    result = _call_service(service.delete_llm_endpoint, conn, llm_id)
    if _is_error(result):
        return result
    return _ok(deleted=True, llm_id=llm_id)


def _set_llm_responses(
    conn: sqlite3.Connection, llm_id: int, responses: list[dict[str, Any]]
) -> Any:
    return _call_service(service.set_llm_responses, conn, llm_id, responses)


def _call_tool(
    conn: sqlite3.Connection,
    slug: str,
    tool_name: str,
    params: dict[str, Any] | None = None,
) -> Any:
    return _call_service(service.call_tool, conn, slug, tool_name, params or {})


def _get_traffic(conn: sqlite3.Connection, target_slug: str | None = None, limit: int = 100) -> Any:
    return _call_service(service.get_traffic, conn, target_slug, limit)


def _get_traffic_summary(conn: sqlite3.Connection) -> Any:
    return _call_service(service.get_traffic_summary, conn)


def _clear_traffic(conn: sqlite3.Connection, confirm: bool = False) -> dict[str, Any]:
    if not confirm:
        return _confirm_required("all recorded traffic logs")
    result = _call_service(service.clear_traffic, conn)
    if _is_error(result):
        return result
    return _ok(cleared=True)


def _list_relationships(conn: sqlite3.Connection, server_id: int) -> Any:
    return _call_service(service.list_relationships, conn, server_id)


def _create_relationship(
    conn: sqlite3.Connection,
    server_id: int,
    name: str,
    source_dataset_id: int,
    source_field: str,
    target_dataset_id: int,
    target_field: str,
    expand_name: str,
    inverse_expand_name: str | None = None,
    relation_type: str = "many_to_one",
    required: bool = False,
    description: str | None = None,
) -> Any:
    return _call_service(
        service.create_relationship,
        conn,
        server_id,
        name=name,
        source_dataset_id=source_dataset_id,
        source_field=source_field,
        target_dataset_id=target_dataset_id,
        target_field=target_field,
        expand_name=expand_name,
        inverse_expand_name=inverse_expand_name,
        relation_type=relation_type,
        required=required,
        description=description,
    )


def _delete_relationship(conn: sqlite3.Connection, relationship_id: int) -> Any:
    result = _call_service(service.delete_relationship, conn, relationship_id)
    if _is_error(result):
        return result
    return _ok(deleted=True)


def _validate_relationships(conn: sqlite3.Connection, server_id: int) -> Any:
    return _call_service(service.validate_relationships, conn, server_id)


def _ensure_demo_relationships(conn: sqlite3.Connection, server_id: int | None = None) -> Any:
    return _call_service(service.ensure_demo_relationships, conn, server_id)


TOOL_SPECS: list[tuple[str, str, list[ParamSpec], Handler]] = [
    ("list_servers", "List all mock MCP servers with counts.", [], _list_servers),
    (
        "get_server",
        "Get one mock MCP server with its datasets and endpoints.",
        [_param("server_id", int, "Server ID.")],
        _get_server,
    ),
    (
        "create_server",
        "Create a mock MCP server.",
        [
            _param("slug", str, "URL slug for the server."),
            _param("name", str, "Display name for the server."),
            _param("description", str, "Description shown to MCP clients.", ""),
            _param("auth_mode", str, "Authentication mode: none or api_key.", "none"),
        ],
        _create_server,
    ),
    (
        "clone_server",
        "Deep-copy a mock MCP server to a new slug.",
        [
            _param("server_id", int, "Source server ID."),
            _param("slug", str, "Target URL slug for the clone."),
            _param("name", str | None, "Optional display name for the clone.", None),
        ],
        _clone_server,
    ),
    (
        "bulk_clone_server",
        "Deep-copy a mock MCP server into numbered team servers.",
        [
            _param("server_id", int, "Source server ID."),
            _param("prefix", str, "Target slug prefix before the padded counter."),
            _param("count", int, "Number of clones to create, from 1 to 50."),
            _param("start", int, "First counter value.", 1),
        ],
        _bulk_clone_server,
    ),
    ("get_catalog", "List mock MCP servers with catalog counts.", [], _get_catalog),
    ("get_cohort", "List bootcamp handout details for all mock servers.", [], _get_cohort),
    (
        "update_server",
        "Update selected fields on a mock MCP server.",
        [
            _param("server_id", int, "Server ID."),
            _param("slug", str | None, "New URL slug.", None),
            _param("name", str | None, "New display name.", None),
            _param("description", str | None, "New description.", None),
            _param("auth_mode", str | None, "New authentication mode.", None),
        ],
        _update_server,
    ),
    (
        "delete_server",
        "Delete a mock MCP server and its child data when confirmed.",
        [
            _param("server_id", int, "Server ID."),
            _param("confirm", bool, "Set true to delete the server.", False),
        ],
        _delete_server,
    ),
    (
        "get_connection_info",
        "Get paste-ready Copilot Studio MCP connection details for a server slug.",
        [_param("slug", str, "Server slug.")],
        _get_connection_info,
    ),
    (
        "list_datasets",
        "List datasets for a mock MCP server.",
        [_param("server_id", int, "Server ID.")],
        _list_datasets,
    ),
    (
        "get_dataset",
        "Get one dataset with row, seed, and field schema details.",
        [_param("dataset_id", int, "Dataset ID.")],
        _get_dataset,
    ),
    (
        "create_dataset",
        "Create a dataset for a mock MCP server.",
        [
            _param("server_id", int, "Server ID."),
            _param("key", str, "Dataset key unique within the server."),
            _param("id_field", str, "Field used as the row identifier.", "id"),
            _param("rows", list[dict[str, Any]] | None, "Initial rows and seed rows.", None),
        ],
        _create_dataset,
    ),
    (
        "update_dataset",
        "Update selected fields on a dataset.",
        [
            _param("dataset_id", int, "Dataset ID."),
            _param("key", str | None, "New dataset key.", None),
            _param("id_field", str | None, "New row identifier field.", None),
        ],
        _update_dataset,
    ),
    (
        "delete_dataset",
        "Delete a dataset when confirmed.",
        [
            _param("dataset_id", int, "Dataset ID."),
            _param("confirm", bool, "Set true to delete the dataset.", False),
        ],
        _delete_dataset,
    ),
    (
        "list_rows",
        "List live rows in a dataset.",
        [_param("dataset_id", int, "Dataset ID.")],
        _list_rows,
    ),
    (
        "replace_rows",
        "Replace all live rows in a dataset when confirmed.",
        [
            _param("dataset_id", int, "Dataset ID."),
            _param("rows", list[dict[str, Any]], "Rows to store as the live dataset."),
            _param("confirm", bool, "Set true to replace all live rows.", False),
        ],
        _replace_rows,
    ),
    (
        "add_rows",
        "Append live rows to a dataset.",
        [
            _param("dataset_id", int, "Dataset ID."),
            _param("rows", list[dict[str, Any]], "Rows to append."),
        ],
        _add_rows,
    ),
    (
        "reset_to_seed",
        "Reset live rows from the seed snapshot when confirmed.",
        [
            _param("dataset_id", int, "Dataset ID."),
            _param("confirm", bool, "Set true to reset live rows.", False),
        ],
        _reset_to_seed,
    ),
    (
        "reset_all_to_seed",
        "Reset all matching mock servers to their seed snapshots when confirmed.",
        [
            _param("prefix", str | None, "Optional literal slug prefix filter.", None),
            _param("confirm", bool, "Set true to reset matching servers.", False),
        ],
        _reset_all_to_seed,
    ),
    (
        "save_as_seed",
        "Replace the seed snapshot with current live rows when confirmed.",
        [
            _param("dataset_id", int, "Dataset ID."),
            _param("confirm", bool, "Set true to overwrite seed rows.", False),
        ],
        _save_as_seed,
    ),
    (
        "list_endpoints",
        "List endpoint tools for a mock MCP server.",
        [_param("server_id", int, "Server ID.")],
        _list_endpoints,
    ),
    (
        "get_endpoint",
        "Get one endpoint tool.",
        [_param("endpoint_id", int, "Endpoint ID.")],
        _get_endpoint,
    ),
    (
        "create_endpoint",
        "Create an endpoint tool backed by a dataset.",
        [
            _param("server_id", int, "Server ID."),
            _param("path", str, "HTTP-style path that determines tool type."),
            _param("tool_name", str, "MCP tool name."),
            _param("dataset_id", int, "Dataset ID backing this endpoint."),
            _param("method", str, "HTTP method.", "GET"),
            _param("description", str, "Tool description.", ""),
            _param("summary_fields", list[str] | None, "Fields returned by list/search.", None),
        ],
        _create_endpoint,
    ),
    (
        "update_endpoint",
        "Update selected fields on an endpoint tool.",
        [
            _param("endpoint_id", int, "Endpoint ID."),
            _param("path", str | None, "New path.", None),
            _param("method", str | None, "New HTTP method.", None),
            _param("tool_name", str | None, "New MCP tool name.", None),
            _param("description", str | None, "New tool description.", None),
            _param("dataset_id", int | None, "New dataset ID.", None),
            _param("summary_fields", list[str] | None, "New list/search summary fields.", None),
        ],
        _update_endpoint,
    ),
    (
        "delete_endpoint",
        "Delete an endpoint tool when confirmed.",
        [
            _param("endpoint_id", int, "Endpoint ID."),
            _param("confirm", bool, "Set true to delete the endpoint.", False),
        ],
        _delete_endpoint,
    ),
    ("list_llm_endpoints", "List OpenAI-compatible LLM endpoints.", [], _list_llm_endpoints),
    (
        "get_llm_endpoint",
        "Get one OpenAI-compatible LLM endpoint with configured responses.",
        [_param("llm_id", int, "LLM endpoint ID.")],
        _get_llm_endpoint,
    ),
    (
        "create_llm_endpoint",
        "Create an OpenAI-compatible LLM endpoint.",
        [
            _param("slug", str, "URL slug for the LLM endpoint."),
            _param("name", str, "Display name for the LLM endpoint."),
            _param("description", str, "Description for admins.", ""),
            _param("mode", str, "Endpoint mode: mock or proxy.", "mock"),
            _param("model_name", str, "Model name exposed to clients.", "playground-model"),
            _param("upstream_url", str | None, "Proxy upstream URL.", None),
            _param("upstream_key", str | None, "Proxy upstream API key.", None),
            _param("upstream_deployment", str | None, "Proxy upstream deployment.", None),
            _param("system_prompt", str | None, "System prompt for mock responses.", None),
            _param("auth_mode", str, "Authentication mode: none or api_key.", "none"),
        ],
        _create_llm_endpoint,
    ),
    (
        "update_llm_endpoint",
        "Update selected fields on an OpenAI-compatible LLM endpoint.",
        [
            _param("llm_id", int, "LLM endpoint ID."),
            _param("slug", str | None, "New URL slug.", None),
            _param("name", str | None, "New display name.", None),
            _param("description", str | None, "New description.", None),
            _param("mode", str | None, "New mode: mock or proxy.", None),
            _param("model_name", str | None, "New model name.", None),
            _param("upstream_url", str | None, "New proxy upstream URL.", None),
            _param("upstream_key", str | None, "New proxy upstream API key.", None),
            _param("upstream_deployment", str | None, "New proxy deployment.", None),
            _param("system_prompt", str | None, "New system prompt.", None),
            _param("auth_mode", str | None, "New authentication mode.", None),
        ],
        _update_llm_endpoint,
    ),
    (
        "delete_llm_endpoint",
        "Delete an OpenAI-compatible LLM endpoint when confirmed.",
        [
            _param("llm_id", int, "LLM endpoint ID."),
            _param("confirm", bool, "Set true to delete the LLM endpoint.", False),
        ],
        _delete_llm_endpoint,
    ),
    (
        "set_llm_responses",
        "Replace the response rules for an OpenAI-compatible LLM endpoint.",
        [
            _param("llm_id", int, "LLM endpoint ID."),
            _param("responses", list[dict[str, Any]], "Response rules to configure."),
        ],
        _set_llm_responses,
    ),
    (
        "call_tool",
        "Call one endpoint tool on a mock MCP server.",
        [
            _param("slug", str, "Server slug."),
            _param("tool_name", str, "Tool name to call."),
            _param("params", dict[str, Any] | None, "Tool parameters.", None),
        ],
        _call_tool,
    ),
    (
        "get_traffic",
        "List recorded traffic logs.",
        [
            _param("target_slug", str | None, "Optional server or LLM slug filter.", None),
            _param("limit", int, "Maximum number of log entries.", 100),
        ],
        _get_traffic,
    ),
    (
        "get_traffic_summary",
        "Aggregate recorded traffic by target slug.",
        [],
        _get_traffic_summary,
    ),
    (
        "clear_traffic",
        "Clear all recorded traffic logs when confirmed.",
        [_param("confirm", bool, "Set true to clear all traffic logs.", False)],
        _clear_traffic,
    ),
    (
        "list_relationships",
        "List all dataset relationships for a server.",
        [_param("server_id", int, "Server ID.")],
        _list_relationships,
    ),
    (
        "create_relationship",
        "Create a dataset relationship linking a source field to a target dataset field.",
        [
            _param("server_id", int, "Server ID."),
            _param("name", str, "Unique name for the relationship within the server."),
            _param("source_dataset_id", int, "ID of the source dataset."),
            _param("source_field", str, "Field name in the source dataset."),
            _param("target_dataset_id", int, "ID of the target dataset."),
            _param("target_field", str, "Field name in the target dataset."),
            _param("expand_name", str, "Name used to expand this relationship on source rows."),
            _param(
                "inverse_expand_name",
                str | None,
                "Name for inverse expand on target rows.",
                None,
            ),
            _param(
                "relation_type",
                str,
                "Relationship type: many_to_one or one_to_one.",
                "many_to_one",
            ),
            _param("required", bool, "Whether the source field is required.", False),
            _param("description", str | None, "Optional description.", None),
        ],
        _create_relationship,
    ),
    (
        "delete_relationship",
        "Delete a dataset relationship by ID.",
        [_param("relationship_id", int, "Relationship ID.")],
        _delete_relationship,
    ),
    (
        "validate_relationships",
        "Validate all dataset relationships for a server and return a health report.",
        [_param("server_id", int, "Server ID.")],
        _validate_relationships,
    ),
    (
        "ensure_demo_relationships",
        (
            "Idempotently create demo relationships for seeded servers."
            " Pass server_id to scope to one server."
        ),
        [_param("server_id", int | None, "Server ID, or null for all servers.", None)],
        _ensure_demo_relationships,
    ),
]


def build_admin_server(conn: sqlite3.Connection) -> FastMCP:
    """Build the stateless management MCP server for MCP Playground."""
    mcp = FastMCP(
        name="MCP Playground Admin",
        instructions=ADMIN_INSTRUCTIONS,
        stateless_http=True,
        json_response=True,
    )

    for name, description, params, handler in TOOL_SPECS:
        mcp.add_tool(_make_tool(conn, name, description, params, handler))

    logger.debug(f"built admin mcp server with {len(mcp._tool_manager._tools)} tools")
    return mcp
