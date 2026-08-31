import sqlite3

import pytest

import db


def test_default_journal_mode_is_wal(tmp_path, monkeypatch):
    monkeypatch.delenv("SQLITE_JOURNAL_MODE", raising=False)
    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT", raising=False)
    conn = db.connect(str(tmp_path / "default.db"))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_delete_journal_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")
    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT", raising=False)
    conn = db.connect(str(tmp_path / "delete.db"))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()


def test_lowercase_journal_mode_is_normalised(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "delete")
    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT", raising=False)
    conn = db.connect(str(tmp_path / "lowercase.db"))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()


def test_journal_mode_strips_whitespace(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", " delete ")
    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT", raising=False)
    conn = db.connect(str(tmp_path / "whitespace.db"))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()


def test_invalid_journal_mode_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "BOGUS")
    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT", raising=False)
    with pytest.raises(ValueError):
        db.connect(str(tmp_path / "invalid.db"))


def test_journal_mode_injection_attempt_raises_without_executing(tmp_path, monkeypatch):
    path = str(tmp_path / "injection.db")
    conn = db.init_db(path)
    conn.close()

    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "WAL; DROP TABLE server")
    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT", raising=False)
    with pytest.raises(ValueError):
        db.connect(path)

    raw = sqlite3.connect(path)
    try:
        row = raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='server'"
        ).fetchone()
        assert row is not None
    finally:
        raw.close()


def test_busy_timeout_is_configurable(tmp_path, monkeypatch):
    monkeypatch.delenv("SQLITE_JOURNAL_MODE", raising=False)
    monkeypatch.setenv("SQLITE_BUSY_TIMEOUT", "12345")
    conn = db.connect(str(tmp_path / "busy-timeout.db"))
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 12345
    finally:
        conn.close()


def test_full_crud_cycle_in_delete_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")
    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT", raising=False)
    conn = db.connect(str(tmp_path / "crud.db"))
    try:
        db.migrate(conn)
        conn.execute(
            "INSERT INTO server (slug, name, description, auth_mode) VALUES (?, ?, ?, ?)",
            ("acme", "Acme", "desc", "none"),
        )
        conn.commit()

        row = conn.execute("SELECT slug, name FROM server WHERE slug = ?", ("acme",)).fetchone()
        assert dict(row) == {"slug": "acme", "name": "Acme"}

        conn.execute(
            "INSERT INTO server (slug, name, description, auth_mode) VALUES (?, ?, ?, ?)",
            ("beta", "Beta", "desc", "none"),
        )
        conn.rollback()

        assert conn.execute("SELECT COUNT(*) FROM server WHERE slug = 'beta'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM server WHERE slug = 'acme'").fetchone()[0] == 1
    finally:
        conn.close()
