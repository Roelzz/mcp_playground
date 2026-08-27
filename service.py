"""Business logic and validation. The only module allowed to enforce rules.

`api.py` and `admin_mcp.py` are thin wrappers over this. Neither may add logic.
"""

import re
import sqlite3
import time
from typing import Any

from loguru import logger

import executor
import store
from db import transaction

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
TOOL_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
LIST_LIKE = {"list", "search"}


class ServiceError(Exception):
    """Raised when a caller violates a business rule."""

    def __init__(self, message: str, code: str = "invalid") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class NotFound(ServiceError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="not_found")


class Conflict(ServiceError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="conflict")


def _validate_slug(slug: str) -> None:
    if slug.startswith("_"):
        raise ServiceError(f"slug {slug!r} is reserved: slugs may not start with '_'")
    if not SLUG_RE.match(slug):
        raise ServiceError(
            f"invalid slug {slug!r}: use lowercase letters, digits and hyphens, "
            "starting with a letter or digit"
        )


def _validate_tool_name(tool_name: str) -> None:
    if not TOOL_NAME_RE.match(tool_name):
        raise ServiceError(
            f"invalid tool_name {tool_name!r}: must be a valid identifier "
            "(letters, digits, underscore; not starting with a digit)"
        )


def _require_server(conn: sqlite3.Connection, server_id: int) -> dict[str, Any]:
    server = store.get_server(conn, server_id)
    if server is None:
        raise NotFound(f"server {server_id} not found")
    return server


def _require_dataset(conn: sqlite3.Connection, dataset_id: int) -> dict[str, Any]:
    dataset = store.get_dataset(conn, dataset_id)
    if dataset is None:
        raise NotFound(f"dataset {dataset_id} not found")
    return dataset


def _require_endpoint(conn: sqlite3.Connection, endpoint_id: int) -> dict[str, Any]:
    endpoint = store.get_endpoint(conn, endpoint_id)
    if endpoint is None:
        raise NotFound(f"endpoint {endpoint_id} not found")
    return endpoint


def _require_llm(conn: sqlite3.Connection, llm_id: int) -> dict[str, Any]:
    llm = store.get_llm_endpoint(conn, llm_id)
    if llm is None:
        raise NotFound(f"llm endpoint {llm_id} not found")
    return llm


# --------------------------------------------------------------------------- servers


def list_servers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    servers = store.list_servers(conn)
    for server in servers:
        datasets = store.list_datasets(conn, server["id"])
        server["dataset_count"] = len(datasets)
        server["endpoint_count"] = len(store.list_endpoints(conn, server["id"]))
        server["row_count"] = sum(store.count_rows(conn, d["id"]) for d in datasets)
    return servers


def get_server(conn: sqlite3.Connection, server_id: int) -> dict[str, Any]:
    server = _require_server(conn, server_id)
    server["datasets"] = list_datasets(conn, server_id)
    server["endpoints"] = list_endpoints(conn, server_id)
    return server


def get_server_by_slug(conn: sqlite3.Connection, slug: str) -> dict[str, Any]:
    server = store.get_server_by_slug(conn, slug)
    if server is None:
        raise NotFound(f"server {slug!r} not found")
    return server


def _clone_server_rows(
    conn: sqlite3.Connection,
    source: dict[str, Any],
    slug: str,
    name: str,
) -> int:
    new_server_id = store.create_server(
        conn,
        slug,
        name,
        str(source["description"]),
        str(source["auth_mode"]),
    )
    dataset_map: dict[int, int] = {}

    for dataset in store.list_datasets(conn, int(source["id"])):
        new_dataset_id = store.create_dataset(
            conn,
            new_server_id,
            str(dataset["key"]),
            str(dataset["id_field"]),
        )
        dataset_map[int(dataset["id"])] = new_dataset_id
        conn.execute(
            "INSERT INTO dataset_row (dataset_id, data) "
            "SELECT ?, data FROM dataset_row WHERE dataset_id = ? ORDER BY id",
            (new_dataset_id, int(dataset["id"])),
        )
        conn.execute(
            "INSERT INTO dataset_seed_row (dataset_id, data) "
            "SELECT ?, data FROM dataset_seed_row WHERE dataset_id = ? ORDER BY id",
            (new_dataset_id, int(dataset["id"])),
        )

    for endpoint in store.list_endpoints(conn, int(source["id"])):
        source_dataset_id = int(endpoint["dataset_id"])
        if source_dataset_id not in dataset_map:
            raise ServiceError(
                f"endpoint {endpoint['tool_name']!r} references dataset outside source server"
            )
        store.create_endpoint(
            conn,
            new_server_id,
            str(endpoint["path"]),
            str(endpoint["method"]),
            str(endpoint["tool_name"]),
            str(endpoint["description"]),
            dataset_map[source_dataset_id],
            endpoint["summary_fields"],
        )

    return new_server_id


def clone_server(
    conn: sqlite3.Connection,
    server_id: int,
    slug: str,
    name: str | None = None,
) -> dict[str, Any]:
    source = _require_server(conn, server_id)
    _validate_slug(slug)
    clone_name = name if name is not None and name.strip() else str(source["name"])
    with transaction(conn):
        if store.get_server_by_slug(conn, slug) is not None:
            raise Conflict(f"server slug {slug!r} already exists")
        new_server_id = _clone_server_rows(conn, source, slug, clone_name)
    logger.info(f"cloned server {source['slug']!r} to {slug!r} (id={new_server_id})")
    return get_server(conn, new_server_id)


def _bulk_clone_targets(prefix: str, count: int, start: int) -> list[tuple[str, str]]:
    largest = start + count - 1
    width = max(2, len(str(largest)))
    return [
        (f"{prefix}{counter:0{width}d}", f"{counter:0{width}d}")
        for counter in range(start, start + count)
    ]


def bulk_clone_server(
    conn: sqlite3.Connection,
    server_id: int,
    prefix: str,
    count: int,
    start: int = 1,
) -> dict[str, list[dict[str, Any]]]:
    if count < 1 or count > 50:
        raise ServiceError("count must be between 1 and 50")
    source = _require_server(conn, server_id)
    targets = _bulk_clone_targets(prefix, count, start)
    for slug, _ in targets:
        _validate_slug(slug)

    with transaction(conn):
        existing = [slug for slug, _ in targets if store.get_server_by_slug(conn, slug) is not None]
        if existing:
            raise Conflict(f"server slug {existing[0]!r} already exists")
        server_ids = [
            _clone_server_rows(conn, source, slug, f"{source['name']} {padded}")
            for slug, padded in targets
        ]

    logger.info(f"bulk cloned server {source['slug']!r} into {len(server_ids)} servers")
    return {"created": [get_server(conn, new_server_id) for new_server_id in server_ids]}


def get_catalog(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return list_servers(conn)


def create_server(
    conn: sqlite3.Connection,
    slug: str,
    name: str,
    description: str = "",
    auth_mode: str = "none",
) -> dict[str, Any]:
    _validate_slug(slug)
    if store.get_server_by_slug(conn, slug) is not None:
        raise Conflict(f"server slug {slug!r} already exists")
    with transaction(conn):
        server_id = store.create_server(conn, slug, name, description, auth_mode)
    logger.info(f"created server {slug!r} (id={server_id})")
    return get_server(conn, server_id)


def update_server(conn: sqlite3.Connection, server_id: int, **fields: Any) -> dict[str, Any]:
    _require_server(conn, server_id)
    slug = fields.get("slug")
    if slug is not None:
        _validate_slug(slug)
        existing = store.get_server_by_slug(conn, slug)
        if existing is not None and existing["id"] != server_id:
            raise Conflict(f"server slug {slug!r} already exists")
    with transaction(conn):
        store.update_server(conn, server_id, **fields)
    logger.info(f"updated server {server_id}")
    return get_server(conn, server_id)


def delete_server(conn: sqlite3.Connection, server_id: int) -> None:
    server = _require_server(conn, server_id)
    with transaction(conn):
        store.delete_server(conn, server_id)
    logger.info(f"deleted server {server['slug']!r} (id={server_id})")


# -------------------------------------------------------------------------- datasets


def list_datasets(conn: sqlite3.Connection, server_id: int) -> list[dict[str, Any]]:
    _require_server(conn, server_id)
    datasets = store.list_datasets(conn, server_id)
    for dataset in datasets:
        dataset["row_count"] = store.count_rows(conn, dataset["id"])
        dataset["seed_count"] = len(store.list_seed_rows(conn, dataset["id"]))
    return datasets


def get_dataset(conn: sqlite3.Connection, dataset_id: int) -> dict[str, Any]:
    dataset = _require_dataset(conn, dataset_id)
    rows = store.list_rows(conn, dataset["id"])
    dataset["row_count"] = len(rows)
    dataset["seed_count"] = len(store.list_seed_rows(conn, dataset_id))
    dataset["field_schema"] = executor.derive_field_schema(rows)
    return dataset


def create_dataset(
    conn: sqlite3.Connection,
    server_id: int,
    key: str,
    id_field: str = "id",
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _require_server(conn, server_id)
    if not key.strip():
        raise ServiceError("dataset key may not be empty")
    if store.get_dataset_by_key(conn, server_id, key) is not None:
        raise Conflict(f"dataset key {key!r} already exists on this server")
    with transaction(conn):
        dataset_id = store.create_dataset(conn, server_id, key, id_field)
        if rows:
            store.add_rows(conn, dataset_id, rows)
            store.replace_seed_rows(conn, dataset_id, rows)
    logger.info(f"created dataset {key!r} (id={dataset_id}) with {len(rows or [])} rows")
    return get_dataset(conn, dataset_id)


def update_dataset(conn: sqlite3.Connection, dataset_id: int, **fields: Any) -> dict[str, Any]:
    dataset = _require_dataset(conn, dataset_id)
    key = fields.get("key")
    if key is not None:
        existing = store.get_dataset_by_key(conn, dataset["server_id"], key)
        if existing is not None and existing["id"] != dataset_id:
            raise Conflict(f"dataset key {key!r} already exists on this server")
    with transaction(conn):
        store.update_dataset(conn, dataset_id, **fields)
    return get_dataset(conn, dataset_id)


def delete_dataset(conn: sqlite3.Connection, dataset_id: int) -> None:
    dataset = _require_dataset(conn, dataset_id)
    endpoints = [
        e for e in store.list_endpoints(conn, dataset["server_id"]) if e["dataset_id"] == dataset_id
    ]
    if endpoints:
        names = ", ".join(e["tool_name"] for e in endpoints)
        raise Conflict(f"dataset {dataset['key']!r} is still used by endpoints: {names}")
    with transaction(conn):
        store.delete_dataset(conn, dataset_id)
    logger.info(f"deleted dataset {dataset['key']!r} (id={dataset_id})")


# ------------------------------------------------------------------------------ rows


def list_rows(conn: sqlite3.Connection, dataset_id: int) -> list[dict[str, Any]]:
    _require_dataset(conn, dataset_id)
    return store.list_rows(conn, dataset_id)


def replace_rows(
    conn: sqlite3.Connection, dataset_id: int, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    _require_dataset(conn, dataset_id)
    _validate_rows(rows)
    with transaction(conn):
        count = store.replace_rows(conn, dataset_id, rows)
    logger.info(f"replaced dataset {dataset_id} with {count} rows")
    return get_dataset(conn, dataset_id)


def add_rows(
    conn: sqlite3.Connection, dataset_id: int, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    _require_dataset(conn, dataset_id)
    _validate_rows(rows)
    with transaction(conn):
        store.add_rows(conn, dataset_id, rows)
    return get_dataset(conn, dataset_id)


def _validate_rows(rows: list[dict[str, Any]]) -> None:
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ServiceError(f"row {i} is {type(row).__name__}, expected an object")


def reset_to_seed(conn: sqlite3.Connection, dataset_id: int) -> dict[str, Any]:
    dataset = _require_dataset(conn, dataset_id)
    with transaction(conn):
        count = store.reset_to_seed(conn, dataset_id)
    logger.info(f"reset dataset {dataset['key']!r} to {count} seed rows")
    return get_dataset(conn, dataset_id)


def save_as_seed(conn: sqlite3.Connection, dataset_id: int) -> dict[str, Any]:
    dataset = _require_dataset(conn, dataset_id)
    with transaction(conn):
        count = store.save_as_seed(conn, dataset_id)
    logger.info(f"saved {count} rows as seed for dataset {dataset['key']!r}")
    return get_dataset(conn, dataset_id)


# ------------------------------------------------------------------------- endpoints


def list_endpoints(conn: sqlite3.Connection, server_id: int) -> list[dict[str, Any]]:
    _require_server(conn, server_id)
    endpoints = store.list_endpoints(conn, server_id)
    for endpoint in endpoints:
        endpoint["tool_type"] = executor.infer_tool_type(endpoint["method"], endpoint["path"])
    return endpoints


def get_endpoint(conn: sqlite3.Connection, endpoint_id: int) -> dict[str, Any]:
    endpoint = _require_endpoint(conn, endpoint_id)
    endpoint["tool_type"] = executor.infer_tool_type(endpoint["method"], endpoint["path"])
    return endpoint


def _validate_endpoint_shape(
    conn: sqlite3.Connection,
    server_id: int,
    path: str,
    method: str,
    dataset_id: int,
    summary_fields: list[str],
) -> str:
    dataset = _require_dataset(conn, dataset_id)
    if dataset["server_id"] != server_id:
        raise ServiceError(
            f"dataset {dataset_id} belongs to server {dataset['server_id']}, not {server_id}"
        )
    try:
        tool_type = executor.infer_tool_type(method, path)
    except executor.ExecutorError as exc:
        raise ServiceError(str(exc)) from exc
    if summary_fields and tool_type not in LIST_LIKE:
        raise ServiceError(
            f"summary_fields is only valid on list/search endpoints, not {tool_type!r}"
        )
    return tool_type


def create_endpoint(
    conn: sqlite3.Connection,
    server_id: int,
    path: str,
    method: str,
    tool_name: str,
    description: str = "",
    dataset_id: int | None = None,
    summary_fields: list[str] | None = None,
) -> dict[str, Any]:
    _require_server(conn, server_id)
    _validate_tool_name(tool_name)
    if dataset_id is None:
        raise ServiceError("dataset_id is required")
    summary_fields = summary_fields or []
    tool_type = _validate_endpoint_shape(
        conn, server_id, path, method, dataset_id, summary_fields
    )
    existing = [e for e in store.list_endpoints(conn, server_id) if e["tool_name"] == tool_name]
    if existing:
        raise Conflict(f"tool_name {tool_name!r} already exists on this server")
    with transaction(conn):
        endpoint_id = store.create_endpoint(
            conn, server_id, path, method, tool_name, description, dataset_id, summary_fields
        )
    logger.info(f"created endpoint {tool_name!r} ({tool_type}) on server {server_id}")
    return get_endpoint(conn, endpoint_id)


def update_endpoint(conn: sqlite3.Connection, endpoint_id: int, **fields: Any) -> dict[str, Any]:
    endpoint = _require_endpoint(conn, endpoint_id)
    server_id = endpoint["server_id"]
    tool_name = fields.get("tool_name")
    if tool_name is not None:
        _validate_tool_name(tool_name)
        clash = [
            e
            for e in store.list_endpoints(conn, server_id)
            if e["tool_name"] == tool_name and e["id"] != endpoint_id
        ]
        if clash:
            raise Conflict(f"tool_name {tool_name!r} already exists on this server")
    merged_summary = fields.get("summary_fields")
    if merged_summary is None:
        merged_summary = endpoint["summary_fields"]
    _validate_endpoint_shape(
        conn,
        server_id,
        fields.get("path") or endpoint["path"],
        fields.get("method") or endpoint["method"],
        fields.get("dataset_id") or endpoint["dataset_id"],
        merged_summary,
    )
    with transaction(conn):
        store.update_endpoint(conn, endpoint_id, **fields)
    return get_endpoint(conn, endpoint_id)


def delete_endpoint(conn: sqlite3.Connection, endpoint_id: int) -> None:
    endpoint = _require_endpoint(conn, endpoint_id)
    with transaction(conn):
        store.delete_endpoint(conn, endpoint_id)
    logger.info(f"deleted endpoint {endpoint['tool_name']!r} (id={endpoint_id})")


# --------------------------------------------------------------------- llm endpoints


def list_llm_endpoints(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return store.list_llm_endpoints(conn)


def get_llm_endpoint(conn: sqlite3.Connection, llm_id: int) -> dict[str, Any]:
    llm = _require_llm(conn, llm_id)
    llm["responses"] = store.list_llm_responses(conn, llm_id)
    return llm


def get_llm_endpoint_by_slug(conn: sqlite3.Connection, slug: str) -> dict[str, Any]:
    llm = store.get_llm_endpoint_by_slug(conn, slug)
    if llm is None:
        raise NotFound(f"llm endpoint {slug!r} not found")
    return llm


def _validate_llm_fields(mode: str | None, upstream_url: str | None) -> None:
    if mode == "proxy" and not upstream_url:
        raise ServiceError("proxy mode requires an upstream_url")


def create_llm_endpoint(conn: sqlite3.Connection, **fields: Any) -> dict[str, Any]:
    slug = fields.get("slug", "")
    _validate_slug(slug)
    if store.get_llm_endpoint_by_slug(conn, slug) is not None:
        raise Conflict(f"llm endpoint slug {slug!r} already exists")
    _validate_llm_fields(fields.get("mode"), fields.get("upstream_url"))
    with transaction(conn):
        llm_id = store.create_llm_endpoint(conn, **fields)
    logger.info(f"created llm endpoint {slug!r} (id={llm_id})")
    return get_llm_endpoint(conn, llm_id)


def update_llm_endpoint(conn: sqlite3.Connection, llm_id: int, **fields: Any) -> dict[str, Any]:
    llm = _require_llm(conn, llm_id)
    slug = fields.get("slug")
    if slug is not None:
        _validate_slug(slug)
        existing = store.get_llm_endpoint_by_slug(conn, slug)
        if existing is not None and existing["id"] != llm_id:
            raise Conflict(f"llm endpoint slug {slug!r} already exists")
    _validate_llm_fields(
        fields.get("mode") or llm["mode"],
        fields.get("upstream_url") or llm["upstream_url"],
    )
    with transaction(conn):
        store.update_llm_endpoint(conn, llm_id, **fields)
    return get_llm_endpoint(conn, llm_id)


def delete_llm_endpoint(conn: sqlite3.Connection, llm_id: int) -> None:
    llm = _require_llm(conn, llm_id)
    with transaction(conn):
        store.delete_llm_endpoint(conn, llm_id)
    logger.info(f"deleted llm endpoint {llm['slug']!r} (id={llm_id})")


def set_llm_responses(
    conn: sqlite3.Connection, llm_id: int, responses: list[dict[str, Any]]
) -> dict[str, Any]:
    _require_llm(conn, llm_id)
    normalised: list[dict[str, Any]] = []
    for i, response in enumerate(responses):
        if not response.get("response"):
            raise ServiceError(f"response {i} has no response text")
        match_type = response.get("match_type") or "always"
        if match_type not in {"always", "contains", "regex"}:
            raise ServiceError(f"response {i} has invalid match_type {match_type!r}")
        if match_type != "always" and not response.get("match_value"):
            raise ServiceError(f"response {i} uses {match_type} but has no match_value")
        normalised.append(
            {
                "match_type": match_type,
                "match_value": response.get("match_value") or "",
                "response": response["response"],
            }
        )
    with transaction(conn):
        store.set_llm_responses(conn, llm_id, normalised)
    return get_llm_endpoint(conn, llm_id)


# ------------------------------------------------------------------------ tool calls


def call_tool(
    conn: sqlite3.Connection,
    slug: str,
    tool_name: str,
    params: dict[str, Any],
    kind: str = "mcp",
    actor: str | None = None,
) -> Any:
    """Resolve a tool by server slug + tool name and execute it. Used by MCP and the UI."""
    started = time.perf_counter()
    status = "ok"
    response: Any = None
    try:
        server = get_server_by_slug(conn, slug)
        matches = [
            e for e in store.list_endpoints(conn, server["id"]) if e["tool_name"] == tool_name
        ]
        if not matches:
            raise NotFound(f"tool {tool_name!r} not found on server {slug!r}")
        endpoint = matches[0]
        dataset = _require_dataset(conn, endpoint["dataset_id"])
        try:
            response = executor.execute(conn, endpoint, dataset, params)
        except executor.ExecutorError as exc:
            raise ServiceError(str(exc), code=exc.code) from exc
        return response
    except ServiceError as exc:
        status = exc.code
        response = {"error": str(exc)}
        raise
    except Exception as exc:
        status = "error"
        response = {"error": str(exc)}
        raise
    finally:
        store.log_call(
            conn,
            kind=kind,
            status=status,
            target_slug=slug,
            tool_name=tool_name,
            request=params,
            response=response,
            duration_ms=int((time.perf_counter() - started) * 1000),
            actor=actor,
        )


# --------------------------------------------------------------------- observability


def get_traffic(
    conn: sqlite3.Connection, target_slug: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    return store.get_traffic(conn, limit=limit, target_slug=target_slug)


def clear_traffic(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        store.clear_traffic(conn)
