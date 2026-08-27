"""SQLite connection factory and schema migrations."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from dotenv import load_dotenv
from loguru import logger

load_dotenv()

SCHEMA_VERSION = 1

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
"""


def db_path() -> str:
    return os.getenv("DB_PATH", "playground.db")


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open a connection with WAL, foreign keys and row access by name."""
    conn = sqlite3.connect(path or db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any exception."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


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
