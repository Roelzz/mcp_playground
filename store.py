"""Plain SQL data access. No business logic, no validation - that lives in service.py."""

import json
import sqlite3
from typing import Any

from db import transaction

# --- helpers ---------------------------------------------------------------


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def _rows(rs: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rs]


def _touch(conn: sqlite3.Connection, table: str, row_id: int) -> None:
    conn.execute(f"UPDATE {table} SET updated_at = datetime('now') WHERE id = ?", (row_id,))


# --- server ----------------------------------------------------------------


def list_servers(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _rows(conn.execute("SELECT * FROM server ORDER BY name").fetchall())


def get_server(conn: sqlite3.Connection, server_id: int) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM server WHERE id = ?", (server_id,)).fetchone())


def get_server_by_slug(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM server WHERE slug = ?", (slug,)).fetchone())


def create_server(
    conn: sqlite3.Connection, slug: str, name: str, description: str, auth_mode: str
) -> int:
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO server (slug, name, description, auth_mode) VALUES (?, ?, ?, ?)",
            (slug, name, description, auth_mode),
        )
    return int(cur.lastrowid)


def update_server(conn: sqlite3.Connection, server_id: int, **fields: Any) -> None:
    allowed = {"slug", "name", "description", "auth_mode"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return
    clause = ", ".join(f"{k} = ?" for k in sets)
    with transaction(conn):
        conn.execute(f"UPDATE server SET {clause} WHERE id = ?", (*sets.values(), server_id))
        _touch(conn, "server", server_id)


def delete_server(conn: sqlite3.Connection, server_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM server WHERE id = ?", (server_id,))


# --- dataset ---------------------------------------------------------------


def list_datasets(conn: sqlite3.Connection, server_id: int) -> list[dict[str, Any]]:
    return _rows(
        conn.execute(
            "SELECT * FROM dataset WHERE server_id = ? ORDER BY key", (server_id,)
        ).fetchall()
    )


def get_dataset(conn: sqlite3.Connection, dataset_id: int) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM dataset WHERE id = ?", (dataset_id,)).fetchone())


def get_dataset_by_key(conn: sqlite3.Connection, server_id: int, key: str) -> dict[str, Any] | None:
    return _row(
        conn.execute(
            "SELECT * FROM dataset WHERE server_id = ? AND key = ?", (server_id, key)
        ).fetchone()
    )


def create_dataset(conn: sqlite3.Connection, server_id: int, key: str, id_field: str) -> int:
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO dataset (server_id, key, id_field) VALUES (?, ?, ?)",
            (server_id, key, id_field),
        )
    return int(cur.lastrowid)


def update_dataset(conn: sqlite3.Connection, dataset_id: int, **fields: Any) -> None:
    allowed = {"key", "id_field"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return
    clause = ", ".join(f"{k} = ?" for k in sets)
    with transaction(conn):
        conn.execute(f"UPDATE dataset SET {clause} WHERE id = ?", (*sets.values(), dataset_id))


def delete_dataset(conn: sqlite3.Connection, dataset_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM dataset WHERE id = ?", (dataset_id,))


def count_rows(conn: sqlite3.Connection, dataset_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM dataset_row WHERE dataset_id = ?", (dataset_id,)
    ).fetchone()
    return int(row["n"])


# --- live rows -------------------------------------------------------------


def list_rows(conn: sqlite3.Connection, dataset_id: int) -> list[dict[str, Any]]:
    rs = conn.execute(
        "SELECT id, data FROM dataset_row WHERE dataset_id = ? ORDER BY id", (dataset_id,)
    ).fetchall()
    return [{"_row_id": r["id"], **json.loads(r["data"])} for r in rs]


def add_rows(conn: sqlite3.Connection, dataset_id: int, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    with transaction(conn):
        conn.executemany(
            "INSERT INTO dataset_row (dataset_id, data) VALUES (?, ?)",
            [(dataset_id, json.dumps(r)) for r in rows],
        )
    return len(rows)


def replace_rows(conn: sqlite3.Connection, dataset_id: int, rows: list[dict[str, Any]]) -> int:
    with transaction(conn):
        conn.execute("DELETE FROM dataset_row WHERE dataset_id = ?", (dataset_id,))
        if rows:
            conn.executemany(
                "INSERT INTO dataset_row (dataset_id, data) VALUES (?, ?)",
                [(dataset_id, json.dumps(r)) for r in rows],
            )
    return len(rows)


def update_row(conn: sqlite3.Connection, row_id: int, data: dict[str, Any]) -> None:
    with transaction(conn):
        conn.execute("UPDATE dataset_row SET data = ? WHERE id = ?", (json.dumps(data), row_id))


def delete_row(conn: sqlite3.Connection, row_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM dataset_row WHERE id = ?", (row_id,))


# --- seed rows -------------------------------------------------------------


def list_seed_rows(conn: sqlite3.Connection, dataset_id: int) -> list[dict[str, Any]]:
    rs = conn.execute(
        "SELECT data FROM dataset_seed_row WHERE dataset_id = ? ORDER BY id", (dataset_id,)
    ).fetchall()
    return [json.loads(r["data"]) for r in rs]


def replace_seed_rows(conn: sqlite3.Connection, dataset_id: int, rows: list[dict[str, Any]]) -> int:
    with transaction(conn):
        conn.execute("DELETE FROM dataset_seed_row WHERE dataset_id = ?", (dataset_id,))
        if rows:
            conn.executemany(
                "INSERT INTO dataset_seed_row (dataset_id, data) VALUES (?, ?)",
                [(dataset_id, json.dumps(r)) for r in rows],
            )
    return len(rows)


def reset_to_seed(conn: sqlite3.Connection, dataset_id: int) -> int:
    """Discard live rows, restore the immutable seed snapshot."""
    with transaction(conn):
        conn.execute("DELETE FROM dataset_row WHERE dataset_id = ?", (dataset_id,))
        conn.execute(
            "INSERT INTO dataset_row (dataset_id, data) "
            "SELECT dataset_id, data FROM dataset_seed_row WHERE dataset_id = ? ORDER BY id",
            (dataset_id,),
        )
    return count_rows(conn, dataset_id)


def save_as_seed(conn: sqlite3.Connection, dataset_id: int) -> int:
    """Promote current live rows to the seed snapshot."""
    with transaction(conn):
        conn.execute("DELETE FROM dataset_seed_row WHERE dataset_id = ?", (dataset_id,))
        conn.execute(
            "INSERT INTO dataset_seed_row (dataset_id, data) "
            "SELECT dataset_id, data FROM dataset_row WHERE dataset_id = ? ORDER BY id",
            (dataset_id,),
        )
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM dataset_seed_row WHERE dataset_id = ?", (dataset_id,)
    ).fetchone()
    return int(row["n"])


# --- endpoint --------------------------------------------------------------


def list_endpoints(conn: sqlite3.Connection, server_id: int) -> list[dict[str, Any]]:
    rs = conn.execute(
        "SELECT * FROM endpoint WHERE server_id = ? ORDER BY tool_name", (server_id,)
    ).fetchall()
    return [{**dict(r), "summary_fields": json.loads(r["summary_fields"])} for r in rs]


def get_endpoint(conn: sqlite3.Connection, endpoint_id: int) -> dict[str, Any] | None:
    r = conn.execute("SELECT * FROM endpoint WHERE id = ?", (endpoint_id,)).fetchone()
    if r is None:
        return None
    return {**dict(r), "summary_fields": json.loads(r["summary_fields"])}


def create_endpoint(
    conn: sqlite3.Connection,
    server_id: int,
    path: str,
    method: str,
    tool_name: str,
    description: str,
    dataset_id: int,
    summary_fields: list[str],
) -> int:
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO endpoint "
            "(server_id, path, method, tool_name, description, dataset_id, summary_fields) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                server_id,
                path,
                method,
                tool_name,
                description,
                dataset_id,
                json.dumps(summary_fields),
            ),
        )
    return int(cur.lastrowid)


def update_endpoint(conn: sqlite3.Connection, endpoint_id: int, **fields: Any) -> None:
    allowed = {"path", "method", "tool_name", "description", "dataset_id", "summary_fields"}
    sets = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not sets:
        return
    if "summary_fields" in sets:
        sets["summary_fields"] = json.dumps(sets["summary_fields"])
    clause = ", ".join(f"{k} = ?" for k in sets)
    with transaction(conn):
        conn.execute(f"UPDATE endpoint SET {clause} WHERE id = ?", (*sets.values(), endpoint_id))


def delete_endpoint(conn: sqlite3.Connection, endpoint_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM endpoint WHERE id = ?", (endpoint_id,))


# --- llm endpoint ----------------------------------------------------------

_LLM_FIELDS = {
    "slug",
    "name",
    "description",
    "mode",
    "model_name",
    "upstream_url",
    "upstream_key",
    "upstream_deployment",
    "system_prompt",
    "auth_mode",
}


def list_llm_endpoints(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _rows(conn.execute("SELECT * FROM llm_endpoint ORDER BY name").fetchall())


def get_llm_endpoint(conn: sqlite3.Connection, llm_id: int) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM llm_endpoint WHERE id = ?", (llm_id,)).fetchone())


def get_llm_endpoint_by_slug(conn: sqlite3.Connection, slug: str) -> dict[str, Any] | None:
    return _row(conn.execute("SELECT * FROM llm_endpoint WHERE slug = ?", (slug,)).fetchone())


def create_llm_endpoint(conn: sqlite3.Connection, **fields: Any) -> int:
    sets = {k: v for k, v in fields.items() if k in _LLM_FIELDS}
    cols = ", ".join(sets)
    marks = ", ".join("?" for _ in sets)
    with transaction(conn):
        cur = conn.execute(
            f"INSERT INTO llm_endpoint ({cols}) VALUES ({marks})", tuple(sets.values())
        )
    return int(cur.lastrowid)


def update_llm_endpoint(conn: sqlite3.Connection, llm_id: int, **fields: Any) -> None:
    sets = {k: v for k, v in fields.items() if k in _LLM_FIELDS and v is not None}
    if not sets:
        return
    clause = ", ".join(f"{k} = ?" for k in sets)
    with transaction(conn):
        conn.execute(f"UPDATE llm_endpoint SET {clause} WHERE id = ?", (*sets.values(), llm_id))
        _touch(conn, "llm_endpoint", llm_id)


def delete_llm_endpoint(conn: sqlite3.Connection, llm_id: int) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM llm_endpoint WHERE id = ?", (llm_id,))


def list_llm_responses(conn: sqlite3.Connection, llm_id: int) -> list[dict[str, Any]]:
    return _rows(
        conn.execute(
            "SELECT * FROM llm_response WHERE llm_endpoint_id = ? ORDER BY ordinal", (llm_id,)
        ).fetchall()
    )


def set_llm_responses(
    conn: sqlite3.Connection, llm_id: int, responses: list[dict[str, Any]]
) -> int:
    with transaction(conn):
        conn.execute("DELETE FROM llm_response WHERE llm_endpoint_id = ?", (llm_id,))
        for i, r in enumerate(responses):
            conn.execute(
                "INSERT INTO llm_response "
                "(llm_endpoint_id, ordinal, match_type, match_value, response) "
                "VALUES (?, ?, ?, ?, ?)",
                (llm_id, i, r["match_type"], r.get("match_value", ""), r["response"]),
            )
    return len(responses)


# --- call log --------------------------------------------------------------


def log_call(
    conn: sqlite3.Connection,
    kind: str,
    status: str,
    target_slug: str | None = None,
    tool_name: str | None = None,
    request: Any = None,
    response: Any = None,
    duration_ms: int = 0,
    actor: str | None = None,
) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO call_log "
            "(kind, target_slug, tool_name, request, response, status, duration_ms, actor) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                kind,
                target_slug,
                tool_name,
                json.dumps(request) if request is not None else None,
                json.dumps(response) if response is not None else None,
                status,
                duration_ms,
                actor,
            ),
        )


def get_traffic(
    conn: sqlite3.Connection, limit: int = 100, target_slug: str | None = None
) -> list[dict[str, Any]]:
    if target_slug:
        rs = conn.execute(
            "SELECT * FROM call_log WHERE target_slug = ? ORDER BY id DESC LIMIT ?",
            (target_slug, limit),
        ).fetchall()
    else:
        rs = conn.execute("SELECT * FROM call_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rs:
        d = dict(r)
        for k in ("request", "response"):
            if d[k]:
                d[k] = json.loads(d[k])
        out.append(d)
    return out


def clear_traffic(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM call_log")


# --- auth ------------------------------------------------------------------


def get_admin_user(conn: sqlite3.Connection, username: str) -> dict[str, Any] | None:
    return _row(
        conn.execute("SELECT * FROM admin_user WHERE username = ?", (username,)).fetchone()
    )


def count_admin_users(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM admin_user").fetchone()["n"])


def create_admin_user(conn: sqlite3.Connection, username: str, password_hash: str) -> int:
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO admin_user (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
    return int(cur.lastrowid)


def list_api_keys(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _rows(
        conn.execute(
            "SELECT id, label, scope, created_at, last_used_at, revoked_at "
            "FROM api_key ORDER BY created_at DESC"
        ).fetchall()
    )


def create_api_key(conn: sqlite3.Connection, label: str, key_hash: str, scope: str) -> int:
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO api_key (label, key_hash, scope) VALUES (?, ?, ?)",
            (label, key_hash, scope),
        )
    return int(cur.lastrowid)


def get_api_key_by_hash(conn: sqlite3.Connection, key_hash: str) -> dict[str, Any] | None:
    return _row(
        conn.execute(
            "SELECT * FROM api_key WHERE key_hash = ? AND revoked_at IS NULL", (key_hash,)
        ).fetchone()
    )


def touch_api_key(conn: sqlite3.Connection, key_id: int) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE api_key SET last_used_at = datetime('now') WHERE id = ?", (key_id,)
        )


def revoke_api_key(conn: sqlite3.Connection, key_id: int) -> None:
    with transaction(conn):
        conn.execute("UPDATE api_key SET revoked_at = datetime('now') WHERE id = ?", (key_id,))
