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
        body_definition = _body_definition_name(endpoint)
        if body_definition is not None:
            definitions[body_definition] = _schema_for_tool_params(endpoint, dataset)

        expand_names = [
            e["name"] for e in service.available_expands(conn, int(endpoint["dataset_id"]))
        ]
        summary = endpoint.get("description") or tool_name.replace("_", " ").title()
        path = str(endpoint["path"])
        method = str(endpoint["method"]).lower()
        paths.setdefault(path, {})[method] = {
            "operationId": tool_name,
            "summary": summary,
            "x-ms-summary": summary,
            "description": _operation_description(endpoint, dataset),
            "parameters": _parameters_for_endpoint(
                endpoint, dataset, body_definition, expand_names
            ),
            "responses": _responses_for_endpoint(endpoint, dataset),
        }

    swagger: dict[str, Any] = {
        "swagger": "2.0",
        "info": {
            "title": str(server["name"]),
            "description": str(server.get("description") or ""),
            "version": "1.0.0",
        },
        "host": host,
        "basePath": f"/mock/{server['slug']}",
        "schemes": [scheme],
        "consumes": ["application/json"],
        "produces": ["application/json"],
        "paths": paths,
        "definitions": definitions,
    }
    if server["auth_mode"] == "api_key":
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
        "version": 2,
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
        "relationships": _bundle_relationships(conn, server_id, dataset_keys),
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

        raw_rels = bundle.get("relationships")
        rels_spec: list[Any] = raw_rels if isinstance(raw_rels, list) else []
        for rel_spec in rels_spec:
            if not isinstance(rel_spec, dict):
                continue
            src_key = rel_spec.get("source_dataset_key")
            tgt_key = rel_spec.get("target_dataset_key")
            if src_key not in dataset_id_by_key:
                raise service.ServiceError(
                    f"relationship references unknown dataset key {src_key!r}"
                )
            if tgt_key not in dataset_id_by_key:
                raise service.ServiceError(
                    f"relationship references unknown dataset key {tgt_key!r}"
                )
            service.create_relationship(
                conn,
                server_id,
                name=str(rel_spec.get("name") or ""),
                source_dataset_id=dataset_id_by_key[src_key],
                source_field=str(rel_spec.get("source_field") or ""),
                target_dataset_id=dataset_id_by_key[tgt_key],
                target_field=str(rel_spec.get("target_field") or ""),
                relation_type=str(rel_spec.get("relation_type") or "many_to_one"),
                expand_name=str(rel_spec.get("expand_name") or ""),
                inverse_expand_name=rel_spec.get("inverse_expand_name"),
                required=bool(rel_spec.get("required", False)),
                description=rel_spec.get("description"),
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
        build_swagger, conn, server_id, os.getenv("PUBLIC_BASE_URL") or "http://localhost:2009"
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
    path_params = set(_path_param_names(str(endpoint["path"])))
    path_param = _path_param_name(str(endpoint["path"]))
    if path_param is not None:
        path_params.add(path_param)
    properties: dict[str, Any] = {}

    if tool_type in {"create", "update"}:
        for field, type_name in field_schema.items():
            if tool_type == "update" and field in path_params:
                continue
            properties[field] = _property_schema(_swagger_type(type_name), _field_label(field))

    schema: dict[str, Any] = {"type": "object", "additionalProperties": False}
    if properties:
        schema["properties"] = properties
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
    return result_schema


def _parameters_for_endpoint(
    endpoint: dict[str, Any],
    dataset: dict[str, Any],
    body_definition: str | None,
    expand_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    tool_type = str(endpoint["tool_type"])
    parameters = _path_parameters(str(endpoint["path"]), dataset)
    if tool_type in {"list", "search"}:
        parameters.extend(_query_parameters(tool_type, dataset))
    if tool_type in {"list", "search", "get"}:
        parameters.append(_expand_parameter(expand_names or []))
    if body_definition is not None:
        parameters.append(_body_parameter(endpoint, body_definition))
    return parameters


def _path_parameters(path: str, dataset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "in": "path",
            "required": True,
            "description": f"Required {name} value from {path}",
            **_property_schema(_path_param_type(name, dataset), _field_label(name)),
        }
        for name in _path_param_names(path)
    ]


def _query_parameters(tool_type: str, dataset: dict[str, Any]) -> list[dict[str, Any]]:
    parameters = [
        {
            "name": "limit",
            "in": "query",
            "required": False,
            "description": "Maximum number of rows to return",
            **_property_schema("integer", _field_label("limit")),
        }
    ]
    if tool_type == "search":
        parameters.append(
            {
                "name": "q",
                "in": "query",
                "required": False,
                "description": "Text to search for across string fields",
                **_property_schema("string", "Search Query"),
            }
        )
    for field, type_name in dict(dataset.get("field_schema") or {}).items():
        parameters.append(
            {
                "name": field,
                "in": "query",
                "required": False,
                "description": f"Filter by {_field_label(field)}",
                **_property_schema(_swagger_type(type_name), _field_label(field)),
            }
        )
    return parameters


def _expand_parameter(expand_names: list[str]) -> dict[str, Any]:
    if expand_names:
        desc = (
            "Comma-separated expand names to inline related rows into each result. "
            f"Available for this dataset: {', '.join(expand_names)}."
        )
    else:
        desc = (
            "Comma-separated expand names to inline related rows into each result. "
            "Available names differ per dataset."
        )
    return {
        "name": "expand",
        "in": "query",
        "required": False,
        "type": "string",
        "description": desc,
        "x-ms-summary": "Expand",
        "x-ms-visibility": "advanced",
    }


def _body_parameter(endpoint: dict[str, Any], body_definition: str) -> dict[str, Any]:
    tool_type = str(endpoint["tool_type"])
    return {
        "name": "body",
        "in": "body",
        "required": True,
        "x-ms-summary": _field_label("body"),
        "description": f"Fields for the {tool_type} request body.",
        "schema": {"$ref": f"#/definitions/{body_definition}"},
    }


def _responses_for_endpoint(endpoint: dict[str, Any], dataset: dict[str, Any]) -> dict[str, Any]:
    tool_type = str(endpoint["tool_type"])
    status_code = "201" if tool_type == "create" else "200"
    return {
        status_code: {
            "description": "Created" if status_code == "201" else "Success",
            "schema": _schema_for_tool_response(endpoint, dataset),
        }
    }


def _body_definition_name(endpoint: dict[str, Any]) -> str | None:
    if str(endpoint["tool_type"]) not in {"create", "update"}:
        return None
    return _parameter_definition_name(str(endpoint["tool_name"]))


def _property_schema(type_name: str, summary: str) -> dict[str, Any]:
    return {"type": _swagger_type(type_name), "x-ms-summary": summary}


def _path_param_type(param_name: str, dataset: dict[str, Any]) -> str:
    field_schema = dict(dataset.get("field_schema") or {})
    if param_name in field_schema:
        return _swagger_type(str(field_schema[param_name]))
    id_field = str(dataset.get("id_field") or "id")
    if param_name == id_field:
        return _swagger_type(str(field_schema.get(id_field, "string")))
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
    names = _path_param_names(path)
    return names[-1] if names else None


def _path_param_names(path: str) -> list[str]:
    return re.findall(r"\{([^}/]+)\}", path)


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


def _bundle_relationships(
    conn: sqlite3.Connection, server_id: int, dataset_key_by_id: dict[int, str]
) -> list[dict[str, Any]]:
    result = []
    for rel in service.list_relationships(conn, server_id):
        result.append(
            {
                "name": rel["name"],
                "source_dataset_key": dataset_key_by_id[int(rel["source_dataset_id"])],
                "source_field": rel["source_field"],
                "target_dataset_key": dataset_key_by_id[int(rel["target_dataset_id"])],
                "target_field": rel["target_field"],
                "relation_type": rel["relation_type"],
                "expand_name": rel["expand_name"],
                "inverse_expand_name": rel.get("inverse_expand_name"),
                "required": bool(rel.get("required", False)),
                "description": str(rel.get("description") or ""),
            }
        )
    return result


def _validate_bundle(bundle: dict[str, Any]) -> None:
    if not isinstance(bundle, dict):
        raise service.ServiceError("bundle must be a JSON object")
    if bundle.get("format") != "mcp-playground-server":
        raise service.ServiceError("unsupported bundle format")
    if bundle.get("version") not in {1, 2}:
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
