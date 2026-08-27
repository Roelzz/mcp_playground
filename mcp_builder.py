"""Build a FastMCP server per playground server slug and dispatch Streamable HTTP requests."""

from __future__ import annotations

import inspect
import re
import sqlite3
from collections.abc import Callable
from typing import Annotated, Any

from loguru import logger
from mcp.server import FastMCP
from pydantic import Field

import executor
import service

IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
RESERVED_PARAMS = {"limit", "q"}
LIST_LIKE = {"list", "search"}

PY_TYPES: dict[str, type] = {
    "integer": int,
    "number": float,
    "boolean": bool,
    "string": str,
}

REQUIRED = inspect.Parameter.empty


class ToolBuildError(Exception):
    pass


def _py_type(schema_type: str) -> type:
    return PY_TYPES.get(schema_type, str)


def _usable_fields(field_schema: dict[str, str]) -> dict[str, type]:
    return {
        name: _py_type(schema_type)
        for name, schema_type in field_schema.items()
        if IDENTIFIER_RE.match(name) and name not in RESERVED_PARAMS
    }


def _tool_params(
    tool_type: str,
    field_schema: dict[str, str],
    id_field: str,
    path_param: str | None,
) -> list[tuple[str, type, str, Any]]:
    """Return (name, type, description, default) for each param the tool accepts."""
    fields = _usable_fields(field_schema)
    params: list[tuple[str, type, str, Any]] = []

    if tool_type in LIST_LIKE:
        if tool_type == "search":
            params.append(("q", str, "Free-text search across all fields.", None))
        for name, py_type in fields.items():
            params.append((name, py_type, f"Filter on exact {name}.", None))
        params.append(("limit", int, "Maximum number of results to return.", 50))
        return params

    if tool_type == "get" or tool_type == "delete":
        param = path_param or id_field
        py_type = fields.get(id_field, str)
        params.append((param, py_type, f"The {id_field} of the record.", REQUIRED))
        return params

    if tool_type == "create":
        for name, py_type in fields.items():
            optional = name == id_field
            note = " Leave unset to assign automatically." if optional else ""
            params.append((name, py_type, f"Value for {name}.{note}", None))
        return params

    if tool_type == "update":
        param = path_param or id_field
        py_type = fields.get(id_field, str)
        params.append((param, py_type, f"The {id_field} of the record to update.", REQUIRED))
        for name, field_type in fields.items():
            if name == param:
                continue
            params.append((name, field_type, f"New value for {name}.", None))
        return params

    raise ToolBuildError(f"unsupported tool type: {tool_type}")


def _make_tool(
    conn: sqlite3.Connection,
    slug: str,
    tool_name: str,
    description: str,
    params: list[tuple[str, type, str, Any]],
) -> Callable[..., Any]:
    def impl(**kwargs: Any) -> Any:
        supplied = {key: value for key, value in kwargs.items() if value is not None}
        return service.call_tool(conn, slug, tool_name, supplied)

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

    impl.__signature__ = inspect.Signature(signature_params, return_annotation=Any)  # type: ignore[attr-defined]
    impl.__annotations__ = {**annotations, "return": Any}
    impl.__name__ = tool_name
    impl.__doc__ = description or f"{tool_name} on {slug}."
    return impl


def build_server(conn: sqlite3.Connection, slug: str) -> FastMCP:
    """Build a stateless FastMCP server exposing every endpoint of `slug` as a tool."""
    server_row = service.get_server_by_slug(conn, slug)
    mcp = FastMCP(
        name=server_row["name"] or slug,
        instructions=server_row["description"] or "",
        stateless_http=True,
        json_response=True,
    )

    for endpoint in service.list_endpoints(conn, int(server_row["id"])):
        dataset = service.get_dataset(conn, int(endpoint["dataset_id"]))
        path_param = executor.path_param_name(str(endpoint["path"]))
        params = _tool_params(
            str(endpoint["tool_type"]),
            dataset.get("field_schema") or {},
            str(dataset["id_field"]),
            path_param,
        )
        mcp.add_tool(
            _make_tool(
                conn,
                slug,
                str(endpoint["tool_name"]),
                str(endpoint["description"] or ""),
                params,
            )
        )

    logger.debug(f"built mcp server {slug!r} with {len(mcp._tool_manager._tools)} tools")
    return mcp


async def dispatch(
    conn: sqlite3.Connection,
    slug: str,
    scope: dict[str, Any],
    receive: Callable[..., Any],
    send: Callable[..., Any],
) -> None:
    """Serve one Streamable HTTP request against a freshly built server.

    Building per request keeps tools in sync with edits and avoids relying on an
    application lifespan that a lazily created sub-app never participates in.
    """
    mcp = build_server(conn, slug)
    mcp.streamable_http_app()
    async with mcp.session_manager.run():
        await mcp.session_manager.handle_request(scope, receive, send)
