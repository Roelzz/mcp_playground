import contextlib
import sqlite3

import pytest

import db


def test_default_journal_mode_is_wal(tmp_path, monkeypatch):
    monkeypatch.delenv("SQLITE_JOURNAL_MODE", raising=False)
    monkeypatch.delenv("SQLITE_BUSY_TIMEOUT", raising=False)
    monkeypatch.delenv("SQLITE_VFS", raising=False)
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


def test_vfs_defaults_to_none(tmp_path, monkeypatch):
    monkeypatch.delenv("SQLITE_VFS", raising=False)
    conn = db.connect(str(tmp_path / "vfs_default.db"))
    try:
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_invalid_vfs_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_VFS", "bogus-vfs")
    with pytest.raises(ValueError, match="SQLITE_VFS"):
        db.connect(str(tmp_path / "vfs_bad.db"))


def test_vfs_rejects_uri_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_VFS", "unix-dotfile&mode=memory")
    with pytest.raises(ValueError, match="SQLITE_VFS"):
        db.connect(str(tmp_path / "vfs_inj.db"))


def test_dotfile_vfs_allows_concurrent_connections(tmp_path, monkeypatch):
    """Azure Files equivalent: dotfile locking must not block a second connection."""
    monkeypatch.setenv("SQLITE_VFS", "unix-dotfile")
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")
    target = str(tmp_path / "azure_sim.db")
    first = db.connect(target)
    second = None
    try:
        db.migrate(first)
        first.execute(
            "INSERT INTO server (slug, name, description, auth_mode) VALUES (?, ?, ?, ?)",
            ("azure", "Azure", "desc", "none"),
        )
        first.commit()
        second = db.connect(target)
        assert second.execute("SELECT COUNT(*) FROM server").fetchone()[0] == 1
        second.execute(
            "INSERT INTO server (slug, name, description, auth_mode) VALUES (?, ?, ?, ?)",
            ("azure2", "Azure2", "desc", "none"),
        )
        second.commit()
        assert first.execute("SELECT COUNT(*) FROM server").fetchone()[0] == 2
    finally:
        if second is not None:
            second.close()
        first.close()


def test_dotfile_vfs_path_with_spaces(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_VFS", "unix-dotfile")
    target = tmp_path / "my data dir"
    target.mkdir()
    conn = db.connect(str(target / "play ground.db"))
    try:
        db.migrate(conn)
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.close()


def test_wal_is_impossible_under_dotfile_vfs(tmp_path, monkeypatch):
    """WAL needs shared memory; the dotfile VFS has none, so SQLite falls back."""
    monkeypatch.setenv("SQLITE_VFS", "unix-dotfile")
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "WAL")
    conn = db.connect(str(tmp_path / "wal_fallback.db"))
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
    finally:
        conn.close()


# ── WAL -> dotfile VFS migration ──────────────────────────────────────────────


def _make_wal_db(path) -> None:
    with contextlib.closing(sqlite3.connect(str(path))) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("CREATE TABLE keep (value TEXT)")
        conn.execute("INSERT INTO keep (value) VALUES ('precious')")
        conn.commit()


def test_wal_database_cannot_be_opened_by_dotfile_vfs(tmp_path, monkeypatch):
    """Guards the assumption behind ensure_journal_compatible()."""
    target = tmp_path / "wal.db"
    _make_wal_db(target)

    monkeypatch.setenv("SQLITE_VFS", "unix-dotfile")
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")
    with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
        db.connect(str(target))


def test_ensure_journal_compatible_converts_wal_and_keeps_data(tmp_path, monkeypatch):
    target = tmp_path / "wal.db"
    _make_wal_db(target)

    monkeypatch.setenv("SQLITE_VFS", "unix-dotfile")
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")
    assert db.ensure_journal_compatible(str(target)) == "DELETE"

    with contextlib.closing(db.connect(str(target))) as conn:
        assert conn.execute("SELECT value FROM keep").fetchone()[0] == "precious"


def test_init_db_recovers_a_wal_database_on_dotfile_vfs(tmp_path, monkeypatch):
    target = tmp_path / "wal.db"
    _make_wal_db(target)

    monkeypatch.setenv("SQLITE_VFS", "unix-dotfile")
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")
    with contextlib.closing(db.init_db(str(target))) as conn:
        assert conn.execute("SELECT value FROM keep").fetchone()[0] == "precious"
        assert db.current_version(conn) == db.SCHEMA_VERSION


def test_ensure_journal_compatible_is_a_noop_without_vfs(tmp_path, monkeypatch):
    target = tmp_path / "wal.db"
    _make_wal_db(target)

    monkeypatch.delenv("SQLITE_VFS", raising=False)
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "WAL")
    assert db.ensure_journal_compatible(str(target)) is None

    with contextlib.closing(sqlite3.connect(str(target))) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_ensure_journal_compatible_ignores_a_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_VFS", "unix-dotfile")
    monkeypatch.setenv("SQLITE_JOURNAL_MODE", "DELETE")
    assert db.ensure_journal_compatible(str(tmp_path / "absent.db")) is None
