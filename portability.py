"""Export and import MCP Playground servers."""

import os
import re
import sqlite3
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

import auth
import db
import service

router = APIRouter(prefix="/api", tags=["portability"])

STATUS = {"not_found": 404, "conflict": 409, "invalid": 400}
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_SENSITIVE_FIELD_TOKENS = ("api_key", "key_hash", "secret", "token", "password")


Conn = auth.ConnDep
Admin = auth.AdminDep


def _handle(fn, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except service.ServiceError as exc:
        raise HTTPException(status_code=STATUS.get(exc.code, 400), detail=exc.message) from exc


def build_swagger(conn: sqlite3.Connection, server_id: int, base_url: str) -> dict[str, Any]:
    """Build an importable Swagger 2.0 custom connector document for a server."""
    server = service.get_server(conn, server_id)
    parsed = urlparse(base_url)
    scheme = parsed.scheme or "http"
    host = parsed.netloc or parsed.path
    if not host:
        raise service.ServiceError("PUBLIC_BASE_URL must include a host")

    datasets = {
        int(dataset["id"]): service.get_dataset(conn, int(dataset["id"]))
        for dataset in service.list_datasets(conn, server_id)
    }
    definitions: dict[str, Any] = {}
    paths: dict[str, Any] = {}

    for dataset in datasets.values():
        definitions[_dataset_definition_name(dataset)] = _schema_for_dataset(dataset)

    operation_ids: set[str] = set()
    for endpoint in service.list_endpoints(conn, server_id):
        tool_name = str(endpoint["tool_name"])
        if tool_name in operation_ids:
            raise service.ServiceError(f"duplicate operationId {tool_name!r}")
        if _TOOL_NAME_RE.match(tool_name) is None:
            raise service.ServiceError(f"invalid operationId {tool_name!r}")
        operation_ids.add(tool_name)

        dataset = datasets[int(endpoint["dataset_id"])]
        param_definition = _parameter_definition_name(tool_name)
        response_definition = _response_definition_name(tool_name)
        definitions[param_definition] = _schema_for_tool_params(endpoint, dataset)
        definitions[response_definition] = _schema_for_tool_response(endpoint, dataset)

        paths[f"/{tool_name}/call"] = {
            "post": {
                "operationId": tool_name,
                "summary": endpoint.get("description") or tool_name.replace("_", " ").title(),
                "x-ms-summary": endpoint.get("description") or tool_name.replace("_", " ").title(),
                "description": _operation_description(endpoint, dataset),
                "parameters": [
                    {
                        "name": "body",
                        "in": "body",
                        "required": True,
                        "x-ms-summary": "Tool parameters",
                        "description": "Parameters passed to the MCP Playground tool call.",
                        "schema": {"$ref": f"#/definitions/{param_definition}"},
                    }
                ],
                "responses": {
                    "200": {
                        "description": "Success",
                        "schema": {"$ref": f"#/definitions/{response_definition}"},
                    }
                },
            }
        }

    swagger: dict[str, Any] = {
        "swagger": "2.0",
        "info": {
            "title": str(server["name"]),
            "description": str(server.get("description") or ""),
            "version": "1.0.0",
        },
        "host": host,
        "basePath": f"/api/servers/{server['slug']}/tools",
        "schemes": [scheme],
        "consumes": ["application/json"],
        "produces": ["application/json"],
        "paths": paths,
        "definitions": definitions,
    }
    # The callable route lives under /api and always requires an admin API key,
    # regardless of the MCP server's own auth_mode.
    swagger["securityDefinitions"] = {
        "api_key": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    }
    swagger["security"] = [{"api_key": []}]

    logger.info(f"exported swagger for server {server['slug']!r} (id={server_id})")
    return swagger


def build_bundle(conn: sqlite3.Connection, server_id: int) -> dict[str, Any]:
    """Build a self-contained JSON bundle for a server."""
    server = service.get_server(conn, server_id)
    datasets = service.list_datasets(conn, server_id)
    dataset_keys = {int(dataset["id"]): str(dataset["key"]) for dataset in datasets}

    bundle = {
        "format": "mcp-playground-server",
        "version": 1,
        "exported_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "server": {
            "slug": server["slug"],
            "name": server["name"],
            "description": server.get("description", ""),
            "auth_mode": server.get("auth_mode", "none"),
        },
        "datasets": [_bundle_dataset(conn, dataset) for dataset in datasets],
        "endpoints": [
            {
                "path": endpoint["path"],
                "method": endpoint["method"],
                "tool_name": endpoint["tool_name"],
                "description": endpoint.get("description", ""),
                "dataset": dataset_keys[int(endpoint["dataset_id"])],
                "summary_fields": list(endpoint.get("summary_fields") or []),
            }
            for endpoint in service.list_endpoints(conn, server_id)
        ],
    }
    logger.info(f"exported bundle for server {server['slug']!r} (id={server_id})")
    return bundle


def import_bundle(
    conn: sqlite3.Connection, bundle: dict[str, Any], slug: str | None = None
) -> dict[str, Any]:
    """Import a server bundle and return an import summary."""
    _validate_bundle(bundle)
    server_spec = bundle["server"]
    import_slug = slug or _required_str(server_spec, "slug", "server")
    dataset_id_by_key: dict[str, int] = {}

    with db.transaction(conn):
        server = service.create_server(
            conn,
            import_slug,
            _required_str(server_spec, "name", "server"),
            str(server_spec.get("description") or ""),
            str(server_spec.get("auth_mode") or "none"),
        )
        server_id = int(server["id"])

        for dataset_spec in bundle["datasets"]:
            dataset_key = _required_str(dataset_spec, "key", "dataset")
            seed_rows = _clean_rows(dataset_spec.get("seed_rows", []), "dataset.seed_rows")
            rows = _clean_rows(dataset_spec.get("rows", []), "dataset.rows")
            created = service.create_dataset(
                conn,
                server_id,
                dataset_key,
                str(dataset_spec.get("id_field") or "id"),
                seed_rows,
            )
            dataset_id = int(created["id"])
            dataset_id_by_key[dataset_key] = dataset_id
            if rows != seed_rows:
                service.replace_rows(conn, dataset_id, rows)

        for endpoint_spec in bundle["endpoints"]:
            dataset_key = _required_str(endpoint_spec, "dataset", "endpoint")
            if dataset_key not in dataset_id_by_key:
                raise service.ServiceError(f"endpoint references unknown dataset {dataset_key!r}")
            service.create_endpoint(
                conn,
                server_id,
                _required_str(endpoint_spec, "path", "endpoint"),
                _required_str(endpoint_spec, "method", "endpoint"),
                _required_str(endpoint_spec, "tool_name", "endpoint"),
                str(endpoint_spec.get("description") or ""),
                dataset_id_by_key[dataset_key],
                _string_list(endpoint_spec.get("summary_fields", []), "endpoint.summary_fields"),
            )

    imported = service.get_server(conn, server_id)
    logger.info(f"imported bundle as server {imported['slug']!r} (id={server_id})")
    return {
        "server": imported,
        "datasets": len(bundle["datasets"]),
        "endpoints": len(bundle["endpoints"]),
    }


@router.get("/servers/{server_id}/swagger")
def export_swagger(
    server_id: int,
    download: bool = False,
    conn: sqlite3.Connection = Conn,
    _: auth.Principal = Admin,
) -> Any:
    swagger = _handle(
        build_swagger, conn, server_id, os.getenv("PUBLIC_BASE_URL", "http://localhost:2009")
    )
    if not download:
        return swagger
    slug = service.get_server(conn, server_id)["slug"]
    return JSONResponse(
        content=swagger,
        headers={"Content-Disposition": f'attachment; filename="{slug}-swagger.json"'},
    )


@router.get("/servers/{server_id}/export")
def export_bundle(
    server_id: int,
    download: bool = False,
    conn: sqlite3.Connection = Conn,
    _: auth.Principal = Admin,
) -> Any:
    bundle = _handle(build_bundle, conn, server_id)
    if not download:
        return bundle
    slug = bundle["server"]["slug"]
    return JSONResponse(
        content=bundle,
        headers={"Content-Disposition": f'attachment; filename="{slug}-bundle.json"'},
    )


@router.post("/servers/import", status_code=201)
def import_server(
    body: dict[str, Any],
    conn: sqlite3.Connection = Conn,
    _: auth.Principal = Admin,
) -> dict[str, Any]:
    bundle, slug = _parse_import_body(body)
    return _handle(import_bundle, conn, bundle, slug)


def _schema_for_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    properties = {
        field: {"type": _swagger_type(type_name), "x-ms-summary": _field_label(field)}
        for field, type_name in dict(dataset.get("field_schema") or {}).items()
    }
    schema: dict[str, Any] = {
        "type": "object",
        "additionalProperties": True,
    }
    if properties:
        schema["properties"] = properties
    return schema


def _schema_for_tool_params(endpoint: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    tool_type = str(endpoint["tool_type"])
    field_schema = dict(dataset.get("field_schema") or {})
    properties: dict[str, Any] = {}
    required: list[str] = []

    path_param = _path_param_name(str(endpoint["path"]))
    if path_param is not None and tool_type in {"get", "update", "delete"}:
        properties[path_param] = _property_schema(
            _path_param_type(path_param, dataset),
            f"Required {path_param} value from {endpoint['path']}",
        )
        required.append(path_param)

    if tool_type in {"list", "search"}:
        properties["limit"] = _property_schema("integer", "Maximum number of rows to return")
        if tool_type == "search":
            properties["q"] = _property_schema("string", "Text to search for across string fields")
        for field, type_name in field_schema.items():
            properties[field] = _property_schema(
                _swagger_type(type_name), f"Filter by {_field_label(field)}"
            )
    elif tool_type in {"create", "update"}:
        for field, type_name in field_schema.items():
            if field == path_param:
                continue
            properties[field] = _property_schema(_swagger_type(type_name), _field_label(field))

    schema: dict[str, Any] = {"type": "object", "additionalProperties": False}
    if properties:
        schema["properties"] = properties
    if required:
        schema["required"] = required
    return schema


def _schema_for_tool_response(endpoint: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    tool_type = str(endpoint["tool_type"])
    row_ref = {"$ref": f"#/definitions/{_dataset_definition_name(dataset)}"}
    if tool_type in {"list", "search"}:
        result_schema: dict[str, Any] = {"type": "array", "items": row_ref}
    elif tool_type == "delete":
        result_schema = {
            "type": "object",
            "properties": {
                "deleted": {"type": "boolean"},
                str(dataset.get("id_field") or "id"): _property_schema(
                    _path_param_type(str(dataset.get("id_field") or "id"), dataset),
                    _field_label(str(dataset.get("id_field") or "id")),
                ),
            },
            "additionalProperties": True,
        }
    else:
        result_schema = row_ref
    return {
        "type": "object",
        "properties": {"result": result_schema},
        "additionalProperties": False,
    }


def _property_schema(type_name: str, summary: str) -> dict[str, Any]:
    return {"type": _swagger_type(type_name), "x-ms-summary": summary}


def _path_param_type(param_name: str, dataset: dict[str, Any]) -> str:
    id_field = str(dataset.get("id_field") or "id")
    if param_name:
        return _swagger_type(str(dict(dataset.get("field_schema") or {}).get(id_field, "string")))
    return "string"


def _swagger_type(type_name: str) -> str:
    normalized = type_name.lower()
    if normalized in {"str", "string", "text"}:
        return "string"
    if normalized in {"int", "integer"}:
        return "integer"
    if normalized in {"float", "number"}:
        return "number"
    if normalized in {"bool", "boolean"}:
        return "boolean"
    if normalized in {"array", "list"}:
        return "array"
    if normalized in {"object", "dict"}:
        return "object"
    return "string"


def _operation_description(endpoint: dict[str, Any], dataset: dict[str, Any]) -> str:
    description = str(endpoint.get("description") or endpoint["tool_name"])
    return (
        f"{description}\n\n"
        f"Calls MCP Playground tool `{endpoint['tool_name']}` for dataset `{dataset['key']}`."
    )


def _dataset_definition_name(dataset: dict[str, Any]) -> str:
    return f"{_safe_definition_name(str(dataset['key']))}Item"


def _parameter_definition_name(tool_name: str) -> str:
    return f"{_safe_definition_name(tool_name)}Parameters"


def _response_definition_name(tool_name: str) -> str:
    return f"{_safe_definition_name(tool_name)}Response"


def _safe_definition_name(value: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", value)
    joined = "".join(part[:1].upper() + part[1:] for part in parts if part)
    return joined or "Payload"


def _field_label(field: str) -> str:
    return field.replace("_", " ").replace("-", " ").title()


def _path_param_name(path: str) -> str | None:
    last_segment = path.rstrip("/").rsplit("/", 1)[-1]
    if last_segment.startswith("{") and last_segment.endswith("}") and len(last_segment) > 2:
        return last_segment[1:-1]
    return None


def _bundle_dataset(conn: sqlite3.Connection, dataset: dict[str, Any]) -> dict[str, Any]:
    dataset_id = int(dataset["id"])
    return {
        "key": dataset["key"],
        "id_field": dataset.get("id_field", "id"),
        "rows": _strip_row_ids(service.list_rows(conn, dataset_id)),
        "seed_rows": _seed_rows(conn, dataset_id),
    }


def _seed_rows(conn: sqlite3.Connection, dataset_id: int) -> list[dict[str, Any]]:
    copy_conn = sqlite3.connect(":memory:")
    copy_conn.row_factory = sqlite3.Row
    try:
        conn.backup(copy_conn)
        service.reset_to_seed(copy_conn, dataset_id)
        return _strip_row_ids(service.list_rows(copy_conn, dataset_id))
    finally:
        copy_conn.close()


def _strip_row_ids(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_clean_row(row) for row in rows]


def _validate_bundle(bundle: dict[str, Any]) -> None:
    if not isinstance(bundle, dict):
        raise service.ServiceError("bundle must be a JSON object")
    if bundle.get("format") != "mcp-playground-server":
        raise service.ServiceError("unsupported bundle format")
    if bundle.get("version") != 1:
        raise service.ServiceError("unsupported bundle version")
    if not isinstance(bundle.get("server"), dict):
        raise service.ServiceError("bundle.server must be an object")
    if not isinstance(bundle.get("datasets"), list):
        raise service.ServiceError("bundle.datasets must be a list")
    if not isinstance(bundle.get("endpoints"), list):
        raise service.ServiceError("bundle.endpoints must be a list")
    for dataset in bundle["datasets"]:
        if not isinstance(dataset, dict):
            raise service.ServiceError("each dataset must be an object")
    for endpoint in bundle["endpoints"]:
        if not isinstance(endpoint, dict):
            raise service.ServiceError("each endpoint must be an object")


def _required_str(source: dict[str, Any], key: str, context: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or value == "":
        raise service.ServiceError(f"{context}.{key} must be a non-empty string")
    return value


def _clean_rows(value: Any, context: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise service.ServiceError(f"{context} must be a list")
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            raise service.ServiceError(f"{context}[{index}] must be an object")
        rows.append(_clean_row(row))
    return rows


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key != "_row_id" and not any(token in key.lower() for token in _SENSITIVE_FIELD_TOKENS)
    }


def _string_list(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise service.ServiceError(f"{context} must be a list of strings")
    return value


def _parse_import_body(body: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    if not isinstance(body, dict):
        raise service.ServiceError("import body must be a JSON object")
    if "bundle" in body:
        bundle = body["bundle"]
        if not isinstance(bundle, dict):
            raise service.ServiceError("bundle must be a JSON object")
        slug = body.get("slug")
        if slug is not None and not isinstance(slug, str):
            raise service.ServiceError("slug must be a string")
        return bundle, slug
    return body, None
