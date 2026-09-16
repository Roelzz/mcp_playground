"""Plain REST surface for mock endpoints."""

import json
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import auth
import service

Conn = Annotated[sqlite3.Connection, auth.ConnDep]

router = APIRouter(prefix="/mock", tags=["rest"])

STATUS_BY_TOOL_TYPE = {
    "list": 200,
    "search": 200,
    "get": 200,
    "create": 201,
    "update": 200,
    "delete": 200,
}


@router.api_route(
    "/{slug}/{rest_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    include_in_schema=False,
)
async def handle(slug: str, rest_path: str, request: Request, conn: Conn) -> Response:
    body_params = await _body_params(request)
    if isinstance(body_params, JSONResponse):
        return body_params
    return await run_in_threadpool(
        _handle_sync,
        slug,
        rest_path,
        request.method,
        dict(request.query_params),
        request.query_params.getlist("expand"),
        auth._request_api_key(request),
        body_params,
        conn,
    )


def _handle_sync(
    slug: str,
    rest_path: str,
    method: str,
    query_params: dict[str, str],
    expand_list: list[str],
    api_key: str | None,
    body_params: dict[str, Any],
    conn: sqlite3.Connection,
) -> Response:
    try:
        server = service.get_server_by_slug(conn, slug)
    except service.ServiceError as exc:
        return _service_error_response(exc)

    principal = auth._resolve_api_key(conn, api_key) if api_key is not None else None
    if server.get("auth_mode") == "api_key" and principal is None:
        return JSONResponse(
            {"detail": f"api key required for mock server: {slug}"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        endpoints = service.list_endpoints(conn, int(server["id"]))
    except service.ServiceError as exc:
        return _service_error_response(exc)

    request_path = _normalise_request_path(rest_path)
    match = _best_endpoint_match(endpoints, method, request_path)
    if match is None:
        return JSONResponse(
            {"detail": f"no mock endpoint for {method.upper()} {request_path}"},
            status_code=404,
        )

    endpoint, path_params = match

    merged = {**query_params, **body_params, **path_params}
    # Support ?expand=a&expand=b (repeated key) in addition to ?expand=a,b.
    if len(expand_list) > 1:
        merged["expand"] = expand_list
    params = {key: value for key, value in merged.items() if value is not None}

    try:
        result = service.call_tool(
            conn,
            slug,
            str(endpoint["tool_name"]),
            params,
            kind="rest",
            actor=principal.label if api_key is not None and principal is not None else None,
        )
    except service.ServiceError as exc:
        return _service_error_response(exc)

    return JSONResponse(
        result,
        status_code=STATUS_BY_TOOL_TYPE.get(str(endpoint["tool_type"]), 200),
    )


def _normalise_request_path(rest_path: str) -> str:
    stripped = rest_path.strip("/")
    return f"/{stripped}" if stripped else "/"


def _path_segments(path: str) -> list[str]:
    normalised = path if path.startswith("/") else f"/{path}"
    normalised = normalised.rstrip("/")
    if not normalised:
        return []
    return normalised.split("/")[1:]


def _is_placeholder(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}") and len(segment) > 2


def _match_path(endpoint_path: str, request_path: str) -> tuple[dict[str, str], int, int] | None:
    endpoint_segments = _path_segments(endpoint_path)
    request_segments = _path_segments(request_path)
    if len(endpoint_segments) != len(request_segments):
        return None

    captures: dict[str, str] = {}
    placeholders = 0
    literals = 0
    for endpoint_segment, request_segment in zip(endpoint_segments, request_segments, strict=True):
        if _is_placeholder(endpoint_segment):
            captures[endpoint_segment[1:-1]] = request_segment
            placeholders += 1
        elif endpoint_segment == request_segment:
            literals += 1
        else:
            return None
    return captures, placeholders, literals


def _best_endpoint_match(
    endpoints: list[dict[str, Any]], method: str, request_path: str
) -> tuple[dict[str, Any], dict[str, str]] | None:
    matches: list[tuple[int, int, str, str, dict[str, Any], dict[str, str]]] = []
    for endpoint in endpoints:
        if str(endpoint["method"]).upper() != method.upper():
            continue
        path_match = _match_path(str(endpoint["path"]), request_path)
        if path_match is None:
            continue
        captures, placeholders, literals = path_match
        matches.append(
            (
                placeholders,
                -literals,
                str(endpoint["path"]),
                str(endpoint["tool_name"]),
                endpoint,
                captures,
            )
        )

    if not matches:
        return None

    # Prefer literal routes over placeholders so /orders/search beats /orders/{id}.
    matches.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    best = matches[0]
    return best[4], best[5]


async def _body_params(request: Request) -> dict[str, Any] | JSONResponse:
    if request.method.upper() not in {"POST", "PUT", "PATCH"}:
        return {}

    raw_body = await request.body()
    if not raw_body:
        return {}

    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"detail": "request body must be valid JSON"}, status_code=400)

    if not isinstance(body, dict):
        return JSONResponse({"detail": "request body must be a JSON object"}, status_code=400)

    return body


def _service_error_response(exc: service.ServiceError) -> JSONResponse:
    status = 400
    if exc.code == "not_found":
        status = 404
    elif exc.code == "conflict":
        status = 409
    return JSONResponse({"detail": exc.message}, status_code=status)
