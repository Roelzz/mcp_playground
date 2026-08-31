"""SQLite connection factory and schema migrations."""

import os
import sqlite3
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

SCHEMA_VERSION = 3

_JOURNAL_MODES = {"WAL", "DELETE", "TRUNCATE", "PERSIST", "MEMORY"}
_VFS_NAMES = {"unix", "unix-dotfile", "unix-excl", "unix-none"}
_TX_DEPTH: dict[int, int] = {}

SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_user (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS api_key (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    label        TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    scope        TEXT NOT NULL DEFAULT 'admin' CHECK (scope IN ('admin', 'readonly')),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_used_at TEXT,
    revoked_at   TEXT
);

CREATE TABLE IF NOT EXISTS server (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    slug        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    auth_mode   TEXT NOT NULL DEFAULT 'none' CHECK (auth_mode IN ('none', 'api_key')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dataset (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL REFERENCES server(id) ON DELETE CASCADE,
    key       TEXT NOT NULL,
    id_field  TEXT NOT NULL DEFAULT 'id',
    UNIQUE (server_id, key)
);

CREATE TABLE IF NOT EXISTS dataset_row (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL REFERENCES dataset(id) ON DELETE CASCADE,
    data       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_seed_row (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id INTEGER NOT NULL REFERENCES dataset(id) ON DELETE CASCADE,
    data       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS endpoint (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id      INTEGER NOT NULL REFERENCES server(id) ON DELETE CASCADE,
    path           TEXT NOT NULL,
    method         TEXT NOT NULL,
    tool_name      TEXT NOT NULL,
    description    TEXT NOT NULL DEFAULT '',
    dataset_id     INTEGER NOT NULL REFERENCES dataset(id) ON DELETE CASCADE,
    summary_fields TEXT NOT NULL DEFAULT '[]',
    UNIQUE (server_id, tool_name)
);

CREATE TABLE IF NOT EXISTS llm_endpoint (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                TEXT NOT NULL UNIQUE,
    name                TEXT NOT NULL,
    description         TEXT NOT NULL DEFAULT '',
    mode                TEXT NOT NULL CHECK (mode IN ('mock', 'proxy')),
    model_name          TEXT NOT NULL DEFAULT 'playground-model',
    upstream_url        TEXT,
    upstream_key        TEXT,
    upstream_deployment TEXT,
    system_prompt       TEXT,
    auth_mode           TEXT NOT NULL DEFAULT 'none' CHECK (auth_mode IN ('none', 'api_key')),
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS llm_response (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    llm_endpoint_id INTEGER NOT NULL REFERENCES llm_endpoint(id) ON DELETE CASCADE,
    ordinal         INTEGER NOT NULL,
    match_type      TEXT NOT NULL CHECK (match_type IN ('always', 'contains', 'regex')),
    match_value     TEXT NOT NULL DEFAULT '',
    response        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS call_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    target_slug TEXT,
    tool_name   TEXT,
    request     TEXT,
    response    TEXT,
    status      TEXT NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    actor       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_dataset_row_dataset ON dataset_row(dataset_id);
CREATE INDEX IF NOT EXISTS idx_dataset_seed_row_dataset ON dataset_seed_row(dataset_id);
CREATE INDEX IF NOT EXISTS idx_endpoint_server ON endpoint(server_id);
CREATE INDEX IF NOT EXISTS idx_dataset_server ON dataset(server_id);
CREATE INDEX IF NOT EXISTS idx_llm_response_endpoint ON llm_response(llm_endpoint_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_call_log_created ON call_log(created_at DESC);

CREATE TABLE IF NOT EXISTS dataset_relationship (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_id INTEGER NOT NULL REFERENCES server(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    source_dataset_id INTEGER NOT NULL REFERENCES dataset(id) ON DELETE CASCADE,
    source_field TEXT NOT NULL,
    target_dataset_id INTEGER NOT NULL REFERENCES dataset(id) ON DELETE CASCADE,
    target_field TEXT NOT NULL,
    relation_type TEXT NOT NULL DEFAULT 'many_to_one'
        CHECK (relation_type IN ('many_to_one','one_to_one')),
    expand_name TEXT NOT NULL,
    inverse_expand_name TEXT,
    required INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (server_id, name),
    UNIQUE (source_dataset_id, source_field, target_dataset_id, target_field, expand_name)
);
CREATE INDEX IF NOT EXISTS idx_dsrel_server ON dataset_relationship(server_id);
CREATE INDEX IF NOT EXISTS idx_dsrel_source ON dataset_relationship(source_dataset_id);
CREATE INDEX IF NOT EXISTS idx_dsrel_target ON dataset_relationship(target_dataset_id);

CREATE TABLE IF NOT EXISTS recipe (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    slug               TEXT NOT NULL UNIQUE,
    title              TEXT NOT NULL,
    summary            TEXT NOT NULL DEFAULT '',
    department         TEXT NOT NULL DEFAULT '',
    skill              TEXT NOT NULL DEFAULT 'beginner'
        CHECK (skill IN ('beginner','intermediate','advanced')),
    agent_instructions TEXT NOT NULL DEFAULT '',
    example_prompts    TEXT NOT NULL DEFAULT '[]',
    destinations       TEXT NOT NULL DEFAULT '[]',
    published          INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS recipe_tool (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id INTEGER NOT NULL REFERENCES recipe(id) ON DELETE CASCADE,
    server_id INTEGER NOT NULL REFERENCES server(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    ordinal   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (recipe_id, server_id, tool_name)
);
CREATE INDEX IF NOT EXISTS idx_recipe_tool_recipe ON recipe_tool(recipe_id);
CREATE INDEX IF NOT EXISTS idx_recipe_tool_server ON recipe_tool(server_id);
CREATE INDEX IF NOT EXISTS idx_recipe_published ON recipe(published);
"""


def db_path() -> str:
    return os.getenv("DB_PATH", "playground.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open a connection with configured journaling, foreign keys and row access by name."""
    target = path or db_path()
    # On SMB shares (Azure Files) the default VFS relies on POSIX byte-range locks,
    # which CIFS handles unreliably: SQLite then fails with "database is locked".
    # unix-dotfile uses a lock file instead and still allows multiple connections.
    vfs = os.getenv("SQLITE_VFS", "").strip()
    if vfs:
        if vfs not in _VFS_NAMES:
            raise ValueError(f"invalid SQLITE_VFS: {vfs!r}")
        conn = sqlite3.connect(
            f"file:{urllib.parse.quote(target)}?vfs={vfs}", uri=True, check_same_thread=False
        )
    else:
        conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    mode = os.getenv("SQLITE_JOURNAL_MODE", "WAL").strip().upper()
    if mode not in _JOURNAL_MODES:
        raise ValueError(f"invalid SQLITE_JOURNAL_MODE: {mode!r}")
    conn.execute(f"PRAGMA journal_mode = {mode}")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {int(os.getenv('SQLITE_BUSY_TIMEOUT', '5000'))}")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any exception.

    Reentrant: store.py helpers open their own transaction, so a service-level
    multi-step write would otherwise commit halfway and leave orphans behind.
    Only the outermost block commits or rolls back.
    """
    key = id(conn)
    depth = _TX_DEPTH.get(key, 0)
    _TX_DEPTH[key] = depth + 1
    try:
        yield conn
        if depth == 0:
            conn.commit()
    except Exception:
        if depth == 0:
            conn.rollback()
        raise
    finally:
        if depth == 0:
            _TX_DEPTH.pop(key, None)
        else:
            _TX_DEPTH[key] = depth


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    version = conn.execute("SELECT version FROM schema_version").fetchone()
    return version["version"] if version else 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply schema DDL when out of date. Idempotent across boots."""
    existing = current_version(conn)
    if existing == SCHEMA_VERSION:
        logger.debug(f"Schema already at version {existing}")
        return existing

    with transaction(conn):
        conn.executescript(SCHEMA_DDL)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    if existing == 0:
        logger.info(f"Initialised database schema at version {SCHEMA_VERSION}")
    else:
        logger.info(f"Migrated database schema from version {existing} to {SCHEMA_VERSION}")
    return SCHEMA_VERSION


def init_db(path: str | None = None) -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    return conn
