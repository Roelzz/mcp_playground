"""Business logic and validation. The only module allowed to enforce rules.

`api.py` and `admin_mcp.py` are thin wrappers over this. Neither may add logic.
"""

import json
import os
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

    for rel in store.list_relationships(conn, int(source["id"])):
        src_id = int(rel["source_dataset_id"])
        tgt_id = int(rel["target_dataset_id"])
        if src_id not in dataset_map:
            raise ServiceError(
                f"relationship {rel['name']!r} source dataset {src_id} not in clone map"
            )
        if tgt_id not in dataset_map:
            raise ServiceError(
                f"relationship {rel['name']!r} target dataset {tgt_id} not in clone map"
            )
        store.create_relationship(
            conn,
            new_server_id,
            name=rel["name"],
            source_dataset_id=dataset_map[src_id],
            source_field=rel["source_field"],
            target_dataset_id=dataset_map[tgt_id],
            target_field=rel["target_field"],
            relation_type=rel["relation_type"],
            expand_name=rel["expand_name"],
            inverse_expand_name=rel["inverse_expand_name"],
            required=rel["required"],
            description=rel["description"],
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


def _public_base_url() -> str:
    return (os.getenv("PUBLIC_BASE_URL") or "http://localhost:2009").rstrip("/")


def get_cohort(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    base_url = _public_base_url()
    rows = conn.execute(
        """
        WITH live_counts AS (
            SELECT dataset_id, COUNT(*) AS row_count
            FROM dataset_row
            GROUP BY dataset_id
        ),
        seed_counts AS (
            SELECT dataset_id, COUNT(*) AS seed_count
            FROM dataset_seed_row
            GROUP BY dataset_id
        ),
        dataset_counts AS (
            SELECT
                d.server_id,
                COUNT(*) AS dataset_count,
                SUM(COALESCE(live_counts.row_count, 0)) AS row_count,
                SUM(COALESCE(seed_counts.seed_count, 0)) AS seed_count,
                SUM(CASE WHEN COALESCE(seed_counts.seed_count, 0) = 0 THEN 1 ELSE 0 END)
                    AS empty_seed_dataset_count
            FROM dataset d
            LEFT JOIN live_counts ON live_counts.dataset_id = d.id
            LEFT JOIN seed_counts ON seed_counts.dataset_id = d.id
            GROUP BY d.server_id
        ),
        endpoint_counts AS (
            SELECT server_id, COUNT(*) AS endpoint_count
            FROM endpoint
            GROUP BY server_id
        ),
        traffic_counts AS (
            SELECT target_slug, COUNT(*) AS call_count, MAX(created_at) AS last_call_at
            FROM call_log
            GROUP BY target_slug
        )
        SELECT
            s.id,
            s.slug,
            s.name,
            s.description,
            s.auth_mode,
            COALESCE(dataset_counts.dataset_count, 0) AS dataset_count,
            COALESCE(endpoint_counts.endpoint_count, 0) AS endpoint_count,
            COALESCE(dataset_counts.row_count, 0) AS row_count,
            COALESCE(dataset_counts.seed_count, 0) AS seed_count,
            COALESCE(dataset_counts.empty_seed_dataset_count, 0) AS empty_seed_dataset_count,
            COALESCE(traffic_counts.call_count, 0) AS call_count,
            traffic_counts.last_call_at
        FROM server s
        LEFT JOIN dataset_counts ON dataset_counts.server_id = s.id
        LEFT JOIN endpoint_counts ON endpoint_counts.server_id = s.id
        LEFT JOIN traffic_counts ON traffic_counts.target_slug = s.slug
        ORDER BY s.slug ASC
        """
    ).fetchall()
    cohort: list[dict[str, Any]] = []
    for row in rows:
        dataset_count = int(row["dataset_count"])
        row_count = int(row["row_count"])
        seed_count = int(row["seed_count"])
        slug = str(row["slug"])
        cohort.append(
            {
                "id": int(row["id"]),
                "slug": slug,
                "name": str(row["name"]),
                "description": str(row["description"]),
                "auth_mode": str(row["auth_mode"]),
                "mcp_url": f"{base_url}/mcp/{slug}",
                "rest_url": f"{base_url}/mock/{slug}",
                "swagger_url": f"{base_url}/api/servers/{int(row['id'])}/swagger",
                "dataset_count": dataset_count,
                "endpoint_count": int(row["endpoint_count"]),
                "row_count": row_count,
                "seed_count": seed_count,
                "seeded": dataset_count >= 1 and int(row["empty_seed_dataset_count"]) == 0,
                "in_sync": row_count == seed_count,
                "call_count": int(row["call_count"]),
                "last_call_at": row["last_call_at"],
            }
        )
    return cohort


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


def _servers_for_reset(conn: sqlite3.Connection, prefix: str | None) -> list[dict[str, Any]]:
    if prefix is None:
        rows = conn.execute("SELECT id, slug FROM server ORDER BY slug ASC").fetchall()
    else:
        rows = conn.execute(
            "SELECT id, slug FROM server WHERE substr(slug, 1, ?) = ? ORDER BY slug ASC",
            (len(prefix), prefix),
        ).fetchall()
    return [dict(row) for row in rows]


def reset_all_to_seed(conn: sqlite3.Connection, prefix: str | None = None) -> dict[str, Any]:
    reset: list[dict[str, Any]] = []
    with transaction(conn):
        for server in _servers_for_reset(conn, prefix):
            server_id = int(server["id"])
            datasets = store.list_datasets(conn, server_id)
            row_count = 0
            for dataset in datasets:
                reset_dataset = reset_to_seed(conn, int(dataset["id"]))
                row_count += int(reset_dataset["row_count"])
            reset.append(
                {
                    "server_id": server_id,
                    "slug": str(server["slug"]),
                    "datasets": len(datasets),
                    "rows": row_count,
                }
            )

    logger.info(f"reset {len(reset)} servers to seed")
    return {
        "reset": reset,
        "server_count": len(reset),
        "dataset_count": sum(int(server["datasets"]) for server in reset),
        "row_count": sum(int(server["rows"]) for server in reset),
    }


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
    tool_type = _validate_endpoint_shape(conn, server_id, path, method, dataset_id, summary_fields)
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


# --- recipes ----------------------------------------------------------------

_RECIPE_SKILLS = {"beginner", "intermediate", "advanced"}


def _require_recipe(conn: sqlite3.Connection, recipe_id: int) -> dict[str, Any]:
    recipe = store.get_recipe(conn, recipe_id)
    if recipe is None:
        raise NotFound(f"recipe {recipe_id} not found")
    return recipe


def _validate_string_list(name: str, values: Any) -> list[str]:
    if not isinstance(values, list):
        raise ServiceError(f"{name} must be a list")
    result: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str) or not value.strip():
            raise ServiceError(f"{name}[{index}] must be a non-empty string")
        result.append(value)
    return result


def _validate_recipe_fields(
    conn: sqlite3.Connection,
    fields: dict[str, Any],
    recipe_id: int | None = None,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = dict(current or {})
    merged.update(fields)

    slug = str(merged.get("slug", "") or "")
    _validate_slug(slug)
    existing = store.get_recipe_by_slug(conn, slug)
    if existing is not None and int(existing["id"]) != recipe_id:
        raise Conflict(f"recipe slug {slug!r} already exists")

    title = str(merged.get("title", "") or "").strip()
    if not title:
        raise ServiceError("title must not be empty")

    skill = str(merged.get("skill", "beginner") or "beginner")
    if skill not in _RECIPE_SKILLS:
        raise ServiceError(f"skill must be one of {sorted(_RECIPE_SKILLS)}, got {skill!r}")

    normalised = dict(fields)
    if "title" in normalised:
        normalised["title"] = str(normalised["title"]).strip()
    if "example_prompts" in normalised:
        normalised["example_prompts"] = json.dumps(
            _validate_string_list("example_prompts", normalised["example_prompts"])
        )
    if "destinations" in normalised:
        normalised["destinations"] = json.dumps(
            _validate_string_list("destinations", normalised["destinations"])
        )
    return normalised


def _validate_recipe_tools(
    conn: sqlite3.Connection, tools: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    seen: set[tuple[int, str]] = set()
    normalised: list[dict[str, Any]] = []
    endpoint_names_by_server: dict[int, set[str]] = {}

    for ordinal, tool in enumerate(tools):
        server_id = int(tool["server_id"])
        tool_name = str(tool["tool_name"]).strip()
        key = (server_id, tool_name)
        if key in seen:
            raise ServiceError(
                f"duplicate recipe tool reference for server {server_id} and tool {tool_name!r}"
            )
        seen.add(key)

        if store.get_server(conn, server_id) is None:
            raise ServiceError(f"server {server_id} referenced by recipe tool does not exist")
        if server_id not in endpoint_names_by_server:
            endpoint_names_by_server[server_id] = {
                str(endpoint["tool_name"]) for endpoint in store.list_endpoints(conn, server_id)
            }
        if tool_name not in endpoint_names_by_server[server_id]:
            raise ServiceError(
                f"tool {tool_name!r} does not exist as an endpoint on server {server_id}"
            )
        normalised.append({"server_id": server_id, "tool_name": tool_name, "ordinal": ordinal})
    return normalised


def _with_recipe_tools(conn: sqlite3.Connection, recipe: dict[str, Any]) -> dict[str, Any]:
    recipe["tools"] = store.list_recipe_tools(conn, int(recipe["id"]))
    return recipe


def list_recipes(
    conn: sqlite3.Connection, published_only: bool = False
) -> list[dict[str, Any]]:
    recipes = store.list_recipes(conn, published_only=published_only)
    tools_by_recipe = store.list_recipe_tools_bulk(conn, [int(recipe["id"]) for recipe in recipes])
    for recipe in recipes:
        recipe["tools"] = tools_by_recipe.get(int(recipe["id"]), [])
    return recipes


def get_recipe(conn: sqlite3.Connection, recipe_id: int) -> dict[str, Any]:
    return _with_recipe_tools(conn, _require_recipe(conn, recipe_id))


def get_recipe_by_slug(
    conn: sqlite3.Connection, slug: str, published_only: bool = False
) -> dict[str, Any]:
    recipe = store.get_recipe_by_slug(conn, slug)
    if recipe is None or (published_only and not recipe["published"]):
        raise NotFound(f"recipe {slug!r} not found")
    return _with_recipe_tools(conn, recipe)


def create_recipe(conn: sqlite3.Connection, **fields: Any) -> dict[str, Any]:
    tools = fields.pop("tools", []) or []
    defaults = {
        "summary": "",
        "department": "",
        "skill": "beginner",
        "agent_instructions": "",
        "example_prompts": [],
        "destinations": [],
        "published": False,
    }
    write_fields = _validate_recipe_fields(conn, {**defaults, **fields})
    normalised_tools = _validate_recipe_tools(conn, tools)
    with transaction(conn):
        try:
            recipe_id = store.create_recipe(conn, **write_fields)
        except sqlite3.IntegrityError as exc:
            raise Conflict(f"recipe slug {fields.get('slug')!r} already exists") from exc
        store.replace_recipe_tools(conn, recipe_id, normalised_tools)
    logger.info(f"created recipe {write_fields['slug']!r} (id={recipe_id})")
    return get_recipe(conn, recipe_id)


def update_recipe(conn: sqlite3.Connection, recipe_id: int, **fields: Any) -> dict[str, Any]:
    current = _require_recipe(conn, recipe_id)
    tools = fields.pop("tools", None)
    fields = {key: value for key, value in fields.items() if value is not None}
    write_fields = _validate_recipe_fields(conn, fields, recipe_id=recipe_id, current=current)
    normalised_tools = None if tools is None else _validate_recipe_tools(conn, tools)
    with transaction(conn):
        store.update_recipe(conn, recipe_id, **write_fields)
        if normalised_tools is not None:
            store.replace_recipe_tools(conn, recipe_id, normalised_tools)
    logger.info(f"updated recipe {recipe_id}")
    return get_recipe(conn, recipe_id)


def delete_recipe(conn: sqlite3.Connection, recipe_id: int) -> None:
    recipe = _require_recipe(conn, recipe_id)
    with transaction(conn):
        store.delete_recipe(conn, recipe_id)
    logger.info(f"deleted recipe {recipe['slug']!r} (id={recipe_id})")


def set_recipe_tools(
    conn: sqlite3.Connection, recipe_id: int, tools: list[dict[str, Any]]
) -> dict[str, Any]:
    _require_recipe(conn, recipe_id)
    normalised_tools = _validate_recipe_tools(conn, tools)
    with transaction(conn):
        store.replace_recipe_tools(conn, recipe_id, normalised_tools)
    return get_recipe(conn, recipe_id)


def _fence_for_markdown(value: str) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=0)
    return "`" * max(3, longest + 1)


def _build_recipe_handout(conn: sqlite3.Connection, recipe_id: int) -> tuple[str, str]:
    recipe = get_recipe(conn, recipe_id)
    slug = str(recipe["slug"])
    lines = [
        f"# {recipe['title']}",
        "",
        f"**Department:** {recipe['department']} | **Skill:** {recipe['skill']}",
        "",
        str(recipe.get("summary") or ""),
        "",
        (
            "Use the server URL your trainer gave you. Recipes are shared bootcamp"
            " instructions, not cloned per team."
        ),
    ]

    if recipe["published"]:
        lines.extend(["", f"Public recipe: {_public_base_url()}/r/{slug}"])

    instructions = str(recipe.get("agent_instructions") or "")
    fence = _fence_for_markdown(instructions)
    lines.extend(["", "## Agent instructions", "", fence, instructions, fence])

    lines.extend(["", "## Example prompts", ""])
    prompts = [str(prompt) for prompt in recipe.get("example_prompts", [])]
    lines.extend(f"- {prompt}" for prompt in prompts)
    if not prompts:
        lines.append("- No example prompts provided.")

    lines.extend(["", "## MCP tools", ""])
    tools = list(recipe.get("tools", []))
    for tool in tools:
        server_name = str(tool.get("server_name") or "Unknown server")
        server_slug = str(tool.get("server_slug") or "unknown")
        lines.append(f"- `{tool['tool_name']}` — {server_name} (`{server_slug}`)")
    if not tools:
        lines.append("- No MCP tools configured.")

    destinations = [str(destination) for destination in recipe.get("destinations", [])]
    if destinations:
        lines.extend(["", "## Destinations", ""])
        lines.extend(f"- {destination}" for destination in destinations)

    return slug, "\n".join(lines).rstrip() + "\n"


def validate_recipes(conn: sqlite3.Connection) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    try:
        recipes = store.list_recipes(conn)
        tools_by_recipe = store.list_recipe_tools_bulk(
            conn, [int(recipe["id"]) for recipe in recipes]
        )
        endpoint_names_by_server: dict[int, set[str]] = {}
        for recipe in recipes:
            recipe_id = int(recipe["id"])
            for tool in tools_by_recipe.get(recipe_id, []):
                server_id = int(tool["server_id"])
                tool_name = str(tool["tool_name"])
                if tool.get("server_slug") is None:
                    issues.append(
                        {
                            "recipe_id": recipe_id,
                            "slug": recipe["slug"],
                            "severity": "error",
                            "server_id": server_id,
                            "tool_name": tool_name,
                            "message": f"server {server_id} no longer exists",
                        }
                    )
                    continue
                if server_id not in endpoint_names_by_server:
                    endpoint_names_by_server[server_id] = {
                        str(endpoint["tool_name"])
                        for endpoint in store.list_endpoints(conn, server_id)
                    }
                if tool_name not in endpoint_names_by_server[server_id]:
                    issues.append(
                        {
                            "recipe_id": recipe_id,
                            "slug": recipe["slug"],
                            "severity": "error",
                            "server_id": server_id,
                            "tool_name": tool_name,
                            "message": (
                                f"tool {tool_name!r} no longer exists as an endpoint"
                                f" on server {server_id}"
                            ),
                        }
                    )
        return {
            "recipe_count": len(recipes),
            "ok": not any(issue["severity"] == "error" for issue in issues),
            "issues": issues,
        }
    except Exception as exc:
        logger.error(f"recipe validation failed: {exc}")
        return {
            "recipe_count": 0,
            "ok": False,
            "issues": [{"severity": "error", "message": f"validation failed: {exc}"}],
        }


def recipe_departments(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT department, COUNT(*) AS count
        FROM recipe
        GROUP BY department
        ORDER BY department
        """
    ).fetchall()
    return [{"department": str(row["department"]), "count": int(row["count"])} for row in rows]


def recipe_counts_by_server(conn: sqlite3.Connection) -> dict[int, int]:
    return store.count_recipes_by_server(conn)


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


def get_traffic_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            target_slug,
            COUNT(*) AS call_count,
            SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) AS ok_count,
            SUM(CASE WHEN status = 'ok' THEN 0 ELSE 1 END) AS error_count,
            MAX(created_at) AS last_call_at,
            ROUND(AVG(duration_ms), 0) AS avg_duration_ms
        FROM call_log
        GROUP BY target_slug
        ORDER BY target_slug IS NULL ASC, call_count DESC, target_slug ASC
        """
    ).fetchall()
    return [
        {
            "target_slug": row["target_slug"],
            "call_count": int(row["call_count"]),
            "ok_count": int(row["ok_count"]),
            "error_count": int(row["error_count"]),
            "last_call_at": row["last_call_at"],
            "avg_duration_ms": int(row["avg_duration_ms"] or 0),
        }
        for row in rows
    ]


def clear_traffic(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        store.clear_traffic(conn)


# --- relationships -----------------------------------------------------------

_RESERVED_EXPAND = {"expand", "limit", "offset", "_row_id"}

_DEMO_RELATIONSHIPS: dict[frozenset[str], list[dict[str, Any]]] = {
    frozenset({"orders", "order_lines"}): [
        {
            "name": "order_lines_to_orders",
            "source_key": "order_lines",
            "source_field": "order_id",
            "target_key": "orders",
            "target_field": "id",
            "expand_name": "order",
            "inverse_expand_name": "lines",
        },
    ],
    frozenset({"employees", "time_off_requests", "org_units"}): [
        {
            "name": "employees_manager",
            "source_key": "employees",
            "source_field": "manager_id",
            "target_key": "employees",
            "target_field": "employee_id",
            "expand_name": "manager",
            "inverse_expand_name": "direct_reports",
        },
        {
            "name": "employees_org_unit",
            "source_key": "employees",
            "source_field": "department",
            "target_key": "org_units",
            "target_field": "name",
            "expand_name": "org_unit",
            "inverse_expand_name": "employees",
        },
        {
            "name": "time_off_requests_to_employees",
            "source_key": "time_off_requests",
            "source_field": "employee_id",
            "target_key": "employees",
            "target_field": "employee_id",
            "expand_name": "employee",
            "inverse_expand_name": "time_off_requests",
        },
    ],
    frozenset({"tickets", "assets", "service_catalog"}): [
        {
            "name": "tickets_to_assets",
            "source_key": "tickets",
            "source_field": "asset_tag",
            "target_key": "assets",
            "target_field": "asset_tag",
            "expand_name": "asset",
            "inverse_expand_name": "tickets",
        },
        {
            "name": "tickets_to_service_catalog",
            "source_key": "tickets",
            "source_field": "service_id",
            "target_key": "service_catalog",
            "target_field": "service_id",
            "expand_name": "service",
            "inverse_expand_name": "tickets",
        },
    ],
    frozenset({"accounts", "contacts", "opportunities"}): [
        {
            "name": "contacts_to_accounts",
            "source_key": "contacts",
            "source_field": "account_id",
            "target_key": "accounts",
            "target_field": "account_id",
            "expand_name": "account",
            "inverse_expand_name": "contacts",
        },
        {
            "name": "opportunities_to_accounts",
            "source_key": "opportunities",
            "source_field": "account_id",
            "target_key": "accounts",
            "target_field": "account_id",
            "expand_name": "account",
            "inverse_expand_name": "opportunities",
        },
        {
            "name": "opportunities_to_contacts",
            "source_key": "opportunities",
            "source_field": "primary_contact_id",
            "target_key": "contacts",
            "target_field": "contact_id",
            "expand_name": "primary_contact",
            "inverse_expand_name": "opportunities",
        },
    ],
    frozenset({"expense_reports", "expense_lines", "cost_centres", "approvals"}): [
        {
            "name": "expense_lines_to_reports",
            "source_key": "expense_lines",
            "source_field": "report_id",
            "target_key": "expense_reports",
            "target_field": "report_id",
            "expand_name": "report",
            "inverse_expand_name": "lines",
        },
        {
            "name": "expense_reports_to_cost_centres",
            "source_key": "expense_reports",
            "source_field": "cost_center_id",
            "target_key": "cost_centres",
            "target_field": "cost_center_id",
            "expand_name": "cost_centre",
            "inverse_expand_name": "reports",
        },
        {
            "name": "approvals_to_reports",
            "source_key": "approvals",
            "source_field": "report_id",
            "target_key": "expense_reports",
            "target_field": "report_id",
            "expand_name": "report",
            "inverse_expand_name": "approvals",
        },
    ],
}


def _require_relationship(conn: sqlite3.Connection, relationship_id: int) -> dict[str, Any]:
    rel = store.get_relationship(conn, relationship_id)
    if rel is None:
        raise NotFound(f"relationship {relationship_id} not found")
    return rel


def list_relationships(conn: sqlite3.Connection, server_id: int) -> list[dict[str, Any]]:
    _require_server(conn, server_id)
    return store.list_relationships(conn, server_id)


def get_relationship(conn: sqlite3.Connection, relationship_id: int) -> dict[str, Any]:
    return _require_relationship(conn, relationship_id)


def create_relationship(conn: sqlite3.Connection, server_id: int, **fields: Any) -> dict[str, Any]:
    source_field = str(fields.get("source_field", "") or "").strip()
    target_field = str(fields.get("target_field", "") or "").strip()
    expand_name = str(fields.get("expand_name", "") or "").strip()
    inverse_expand_name = fields.get("inverse_expand_name")
    if inverse_expand_name is not None:
        inverse_expand_name = str(inverse_expand_name).strip() or None

    if not source_field:
        raise ServiceError("source_field must not be empty")
    if not target_field:
        raise ServiceError("target_field must not be empty")
    if not expand_name:
        raise ServiceError("expand_name must not be empty")

    relation_type = fields.get("relation_type", "many_to_one")
    if relation_type not in {"many_to_one", "one_to_one"}:
        raise ServiceError(
            f"relation_type must be 'many_to_one' or 'one_to_one', got {relation_type!r}"
        )

    if expand_name in _RESERVED_EXPAND:
        raise ServiceError(f"expand_name {expand_name!r} is reserved: {sorted(_RESERVED_EXPAND)}")
    if inverse_expand_name is not None and inverse_expand_name in _RESERVED_EXPAND:
        raise ServiceError(
            f"inverse_expand_name {inverse_expand_name!r} is reserved: {sorted(_RESERVED_EXPAND)}"
        )

    src_dataset = store.get_dataset(conn, int(fields["source_dataset_id"]))
    if src_dataset is None:
        raise NotFound(f"source dataset {fields['source_dataset_id']} not found")
    if int(src_dataset["server_id"]) != server_id:
        raise ServiceError(
            f"source dataset {fields['source_dataset_id']} belongs to"
            f" server {src_dataset['server_id']}, not {server_id}"
        )

    tgt_dataset = store.get_dataset(conn, int(fields["target_dataset_id"]))
    if tgt_dataset is None:
        raise NotFound(f"target dataset {fields['target_dataset_id']} not found")
    if int(tgt_dataset["server_id"]) != server_id:
        raise ServiceError(
            f"target dataset {fields['target_dataset_id']} belongs to"
            f" server {tgt_dataset['server_id']}, not {server_id}"
        )

    name = str(fields.get("name", "") or "").strip()
    existing = store.list_relationships(conn, server_id)
    for rel in existing:
        if rel["name"] == name:
            raise Conflict(f"relationship name {name!r} already exists on server {server_id}")
        if (
            rel["source_dataset_id"] == int(fields["source_dataset_id"])
            and rel["expand_name"] == expand_name
        ):
            raise Conflict(
                f"expand_name {expand_name!r} already used on source"
                f" dataset {fields['source_dataset_id']}"
            )
        if (
            inverse_expand_name is not None
            and rel["target_dataset_id"] == int(fields["target_dataset_id"])
            and rel["inverse_expand_name"] == inverse_expand_name
        ):
            raise Conflict(
                f"inverse_expand_name {inverse_expand_name!r} already used on"
                f" target dataset {fields['target_dataset_id']}"
            )

    with transaction(conn):
        rel_id = store.create_relationship(
            conn,
            server_id,
            name=name,
            source_dataset_id=int(fields["source_dataset_id"]),
            source_field=source_field,
            target_dataset_id=int(fields["target_dataset_id"]),
            target_field=target_field,
            relation_type=relation_type,
            expand_name=expand_name,
            inverse_expand_name=inverse_expand_name,
            required=bool(fields.get("required", False)),
            description=fields.get("description"),
        )
    return _require_relationship(conn, rel_id)


def delete_relationship(conn: sqlite3.Connection, relationship_id: int) -> None:
    _require_relationship(conn, relationship_id)
    with transaction(conn):
        store.delete_relationship(conn, relationship_id)


def validate_relationships(conn: sqlite3.Connection, server_id: int) -> dict[str, Any]:
    _require_server(conn, server_id)
    rels = store.list_relationships(conn, server_id)
    issues: list[dict[str, Any]] = []

    for rel in rels:
        tgt_dataset = store.get_dataset(conn, int(rel["target_dataset_id"]))
        if tgt_dataset is None:
            issues.append(
                {
                    "relationship_id": rel["id"],
                    "name": rel["name"],
                    "severity": "error",
                    "message": f"target dataset {rel['target_dataset_id']} no longer exists",
                }
            )
            continue

        src_dataset = store.get_dataset(conn, int(rel["source_dataset_id"]))
        if src_dataset is None:
            issues.append(
                {
                    "relationship_id": rel["id"],
                    "name": rel["name"],
                    "severity": "error",
                    "message": f"source dataset {rel['source_dataset_id']} no longer exists",
                }
            )
            continue

        tgt_rows = store.list_rows(conn, int(rel["target_dataset_id"]))
        src_rows = store.list_rows(conn, int(rel["source_dataset_id"]))
        target_field = rel["target_field"]
        source_field = rel["source_field"]

        if tgt_rows and target_field not in tgt_rows[0]:
            issues.append(
                {
                    "relationship_id": rel["id"],
                    "name": rel["name"],
                    "severity": "warning",
                    "message": f"target field {target_field!r} not found in target dataset rows",
                }
            )

        target_values = {str(r[target_field]) for r in tgt_rows if target_field in r}
        orphan_count = 0
        null_count = 0
        for row in src_rows:
            val = row.get(source_field)
            if val is None:
                if rel["required"]:
                    null_count += 1
            elif str(val) not in target_values:
                orphan_count += 1

        if orphan_count > 0:
            issues.append(
                {
                    "relationship_id": rel["id"],
                    "name": rel["name"],
                    "severity": "warning",
                    "message": (
                        f"{orphan_count} source row(s) have {source_field!r}"
                        " values not found in target"
                    ),
                }
            )
        if null_count > 0:
            issues.append(
                {
                    "relationship_id": rel["id"],
                    "name": rel["name"],
                    "severity": "warning",
                    "message": (
                        f"{null_count} source row(s) have null {source_field!r}"
                        " but relationship is required"
                    ),
                }
            )

    return {
        "ok": not any(i["severity"] == "error" for i in issues),
        "relationship_count": len(rels),
        "issues": issues,
    }


# --- expand helpers -----------------------------------------------------------


def parse_expand(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        names = value
    else:
        names = [v.strip() for v in value.split(",")]
    seen: set[str] = set()
    result: list[str] = []
    for name in names:
        name = name.strip()
        if not name:
            raise ServiceError("expand name must not be empty")
        if "." in name:
            raise ServiceError(
                f"expand name {name!r} contains '.'; only depth-1 expansion is"
                " supported (dot notation not supported)"
            )
        if name not in seen:
            seen.add(name)
            result.append(name)
    if len(result) > 5:
        raise ServiceError(f"at most 5 expand names allowed, got {len(result)}")
    return result


def available_expands(conn: sqlite3.Connection, dataset_id: int) -> list[dict[str, Any]]:
    ds = store.get_dataset(conn, dataset_id)
    if ds is None:
        raise NotFound(f"dataset {dataset_id} not found")
    rels = store.list_relationships_for_dataset(conn, dataset_id)
    result: list[dict[str, Any]] = []
    for rel in rels:
        if int(rel["source_dataset_id"]) == dataset_id:
            tgt = store.get_dataset(conn, int(rel["target_dataset_id"]))
            result.append(
                {
                    "name": rel["expand_name"],
                    "direction": "forward",
                    "relationship_id": rel["id"],
                    "relationship_name": rel["name"],
                    "target_dataset_id": rel["target_dataset_id"],
                    "target_dataset_key": tgt["key"] if tgt else None,
                    "relation_type": rel["relation_type"],
                    "returns": "object",
                }
            )
        if int(rel["target_dataset_id"]) == dataset_id and rel.get("inverse_expand_name"):
            src = store.get_dataset(conn, int(rel["source_dataset_id"]))
            result.append(
                {
                    "name": rel["inverse_expand_name"],
                    "direction": "inverse",
                    "relationship_id": rel["id"],
                    "relationship_name": rel["name"],
                    "target_dataset_id": rel["source_dataset_id"],
                    "target_dataset_key": src["key"] if src else None,
                    "relation_type": rel["relation_type"],
                    "returns": "array",
                }
            )
    return result


def expand_rows(
    conn: sqlite3.Connection,
    dataset_id: int,
    rows: list[dict[str, Any]],
    expand_names: list[str],
) -> list[dict[str, Any]]:
    if not expand_names:
        return rows

    avail = available_expands(conn, dataset_id)
    avail_by_name = {e["name"]: e for e in avail}

    for name in expand_names:
        if name not in avail_by_name:
            valid = sorted(avail_by_name)
            raise ServiceError(
                f"unknown expand name {name!r} for dataset {dataset_id}; valid: {valid}"
            )

    # cache: target_dataset_id -> {str(target_field_val): row_dict}
    forward_cache: dict[int, dict[str, dict[str, Any]]] = {}
    # cache: source_dataset_id -> {str(source_field_val): [row_dict, ...]}
    inverse_cache: dict[int, dict[str, list[dict[str, Any]]]] = {}

    for name in expand_names:
        expand_info = avail_by_name[name]
        if expand_info["direction"] == "forward":
            tgt_id = int(expand_info["target_dataset_id"])
            if tgt_id not in forward_cache:
                rel = store.get_relationship(conn, int(expand_info["relationship_id"]))
                if rel is None:
                    forward_cache[tgt_id] = {}
                    continue
                target_field = rel["target_field"]
                tgt_rows = store.list_rows(conn, tgt_id)
                idx: dict[str, dict[str, Any]] = {}
                for r in tgt_rows:
                    if target_field in r:
                        k = str(r[target_field])
                        child = {kk: vv for kk, vv in r.items() if kk != "_row_id"}
                        idx[k] = child
                forward_cache[tgt_id] = idx
        else:
            src_id = int(expand_info["target_dataset_id"])  # inverse: other dataset is "source"
            if src_id not in inverse_cache:
                rel = store.get_relationship(conn, int(expand_info["relationship_id"]))
                if rel is None:
                    inverse_cache[src_id] = {}
                    continue
                source_field = rel["source_field"]
                target_field = rel["target_field"]
                src_rows = store.list_rows(conn, src_id)
                iidx: dict[str, list[dict[str, Any]]] = {}
                for r in src_rows:
                    if source_field in r and r[source_field] is not None:
                        k = str(r[source_field])
                        child = {kk: vv for kk, vv in r.items() if kk != "_row_id"}
                        iidx.setdefault(k, []).append(child)
                inverse_cache[src_id] = iidx

    result: list[dict[str, Any]] = []
    for row in rows:
        new_row = dict(row)
        for name in expand_names:
            expand_info = avail_by_name[name]
            rel = store.get_relationship(conn, int(expand_info["relationship_id"]))
            if rel is None:
                new_row[name] = None if expand_info["direction"] == "forward" else []
                continue
            if expand_info["direction"] == "forward":
                source_field = rel["source_field"]
                tgt_id = int(expand_info["target_dataset_id"])
                src_val = row.get(source_field)
                if src_val is None:
                    new_row[name] = None
                else:
                    idx = forward_cache.get(tgt_id, {})
                    new_row[name] = idx.get(str(src_val))
            else:
                target_field = rel["target_field"]
                src_id = int(expand_info["target_dataset_id"])
                tgt_val = row.get(target_field)
                if tgt_val is None:
                    new_row[name] = []
                else:
                    iidx = inverse_cache.get(src_id, {})
                    new_row[name] = iidx.get(str(tgt_val), [])
        result.append(new_row)
    return result


def ensure_demo_relationships(
    conn: sqlite3.Connection, server_id: int | None = None
) -> dict[str, Any]:
    servers = (
        [_require_server(conn, server_id)] if server_id is not None else store.list_servers(conn)
    )
    servers_touched = 0
    relationships_created = 0
    skipped_existing = 0

    with transaction(conn):
        for server in servers:
            sid = int(server["id"])
            datasets = store.list_datasets(conn, sid)
            dataset_by_key = {str(d["key"]): d for d in datasets}
            actual_keys = frozenset(dataset_by_key)

            for required_keys, rel_specs in _DEMO_RELATIONSHIPS.items():
                if not (required_keys <= actual_keys):
                    continue
                server_touched_this = False
                for spec in rel_specs:
                    src_ds = dataset_by_key[spec["source_key"]]
                    tgt_ds = dataset_by_key[spec["target_key"]]
                    src_id = int(src_ds["id"])
                    tgt_id = int(tgt_ds["id"])
                    sf = spec["source_field"]
                    tf = spec["target_field"]
                    existing = [
                        r
                        for r in store.list_relationships(conn, sid)
                        if (
                            int(r["source_dataset_id"]) == src_id
                            and r["source_field"] == sf
                            and int(r["target_dataset_id"]) == tgt_id
                            and r["target_field"] == tf
                        )
                    ]
                    if existing:
                        skipped_existing += 1
                        continue
                    store.create_relationship(
                        conn,
                        sid,
                        name=spec["name"],
                        source_dataset_id=src_id,
                        source_field=sf,
                        target_dataset_id=tgt_id,
                        target_field=tf,
                        relation_type="many_to_one",
                        expand_name=spec["expand_name"],
                        inverse_expand_name=spec.get("inverse_expand_name"),
                        required=False,
                    )
                    relationships_created += 1
                    server_touched_this = True
                if server_touched_this:
                    servers_touched += 1

    return {
        "servers_touched": servers_touched,
        "relationships_created": relationships_created,
        "skipped_existing": skipped_existing,
    }
