import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import db


@pytest.fixture(autouse=True)
def reset_auth_state(monkeypatch: pytest.MonkeyPatch) -> None:
    auth._LOGIN_FAILURES.clear()
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    monkeypatch.setenv("INSECURE_COOKIES", "1")


@pytest.fixture
def conn(tmp_path) -> Iterator[sqlite3.Connection]:
    connection = db.init_db(str(tmp_path / "auth.db"))
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def client(conn: sqlite3.Connection) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(auth.router)

    @app.post("/write-check")
    def write_check(_: auth.Principal = auth.WriteDep) -> dict[str, str]:
        return {"ok": "yes"}

    app.dependency_overrides[auth.get_conn] = lambda: conn
    with TestClient(app) as test_client:
        yield test_client


def _create_admin(
    conn: sqlite3.Connection, username: str = "admin", password: str = "secret"
) -> None:
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO admin_user (username, password_hash) VALUES (?, ?)",
            (username, auth.hash_password(password)),
        )


def _login(client: TestClient, username: str = "admin", password: str = "secret") -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text


def _create_api_key(
    conn: sqlite3.Connection, label: str = "demo", scope: auth.Scope = "admin"
) -> tuple[int, str]:
    key = auth.generate_api_key()
    with db.transaction(conn):
        cursor = conn.execute(
            "INSERT INTO api_key (label, key_hash, scope) VALUES (?, ?, ?)",
            (label, auth.hash_api_key(key), scope),
        )
    return int(cursor.lastrowid), key


def test_password_hashing_round_trip_and_malformed_hash() -> None:
    stored = auth.hash_password("correct horse battery staple")

    assert auth.verify_password("correct horse battery staple", stored)
    assert not auth.verify_password("wrong", stored)
    assert not auth.verify_password("password", "not-a-valid-hash")


def test_password_hashes_are_salted() -> None:
    assert auth.hash_password("same") != auth.hash_password("same")


def test_generate_api_key_prefix_and_entropy() -> None:
    first = auth.generate_api_key()
    second = auth.generate_api_key()

    assert first.startswith("mcpp_")
    assert second.startswith("mcpp_")
    assert first != second


def test_bootstrap_creates_user_once_and_honours_env(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOOTSTRAP_USER", "root")
    monkeypatch.setenv("BOOTSTRAP_PASSWORD", "boot-secret")

    auth.bootstrap(conn)
    first = conn.execute("SELECT username, password_hash FROM admin_user").fetchall()
    auth.bootstrap(conn)
    second = conn.execute("SELECT username, password_hash FROM admin_user").fetchall()

    assert len(first) == 1
    assert len(second) == 1
    assert second[0]["username"] == "root"
    assert second[0]["password_hash"] == first[0]["password_hash"]
    assert auth.verify_password("boot-secret", second[0]["password_hash"])


def test_login_good_credentials_sets_cookie_and_bad_password_401(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    _create_admin(conn)

    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret"},
    )
    bad = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong"},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"username": "admin", "scope": "admin"}
    assert auth.SESSION_COOKIE in response.cookies
    assert bad.status_code == 401


def test_me_requires_auth_and_accepts_session_cookie(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    _create_admin(conn)

    assert client.get("/api/auth/me").status_code == 401
    _login(client)
    response = client.get("/api/auth/me")

    assert response.status_code == 200, response.text
    assert response.json() == {"username": "admin", "scope": "admin"}


def test_logout_clears_session(client: TestClient, conn: sqlite3.Connection) -> None:
    _create_admin(conn)
    _login(client)
    assert client.get("/api/auth/me").status_code == 200

    logout = client.post("/api/auth/logout")

    assert logout.status_code == 204, logout.text
    assert client.get("/api/auth/me").status_code == 401


def test_session_survives_restart(tmp_path) -> None:
    db_file = tmp_path / "auth-restart.db"
    first_conn = db.init_db(str(db_file))
    token = auth.create_session(first_conn, "admin", "admin")
    token_hash = auth._hash_session_token(token)
    first_conn.close()

    second_conn = db.init_db(str(db_file))
    try:
        principal = auth.resolve_session(second_conn, token)
        stored = second_conn.execute("SELECT token_hash FROM session").fetchone()
    finally:
        second_conn.close()

    assert principal is not None
    assert principal.label == "admin"
    assert principal.scope == "admin"
    assert stored["token_hash"] == token_hash
    assert stored["token_hash"] != token


def test_expired_session_is_cleaned_up_and_returns_no_principal(
    conn: sqlite3.Connection,
) -> None:
    token = auth.create_session(conn, "admin", "admin")
    with db.transaction(conn):
        conn.execute(
            "UPDATE session SET expires_at = datetime('now', '-1 second') WHERE token_hash = ?",
            (auth._hash_session_token(token),),
        )

    principal = auth.resolve_session(conn, token)
    remaining = conn.execute("SELECT COUNT(*) AS count FROM session").fetchone()

    assert principal is None
    assert remaining["count"] == 0


def test_api_key_auth_works_via_authorization_and_x_api_key(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    _, key = _create_api_key(conn, label="demo-key")

    bearer = client.get("/api/auth/me", headers={"Authorization": f"Bearer {key}"})
    x_api_key = client.get("/api/auth/me", headers={"X-API-Key": key})

    assert bearer.status_code == 200, bearer.text
    assert bearer.json() == {"username": "demo-key", "scope": "admin"}
    assert x_api_key.status_code == 200, x_api_key.text
    assert x_api_key.json() == {"username": "demo-key", "scope": "admin"}


def test_revoked_key_returns_401(client: TestClient, conn: sqlite3.Connection) -> None:
    key_id, key = _create_api_key(conn)
    with db.transaction(conn):
        conn.execute("UPDATE api_key SET revoked_at = datetime('now') WHERE id = ?", (key_id,))

    response = client.get("/api/auth/me", headers={"X-API-Key": key})

    assert response.status_code == 401


def test_readonly_key_can_read_but_cannot_write(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    _, key = _create_api_key(conn, label="read-key", scope="readonly")
    headers = {"X-API-Key": key}

    me = client.get("/api/auth/me", headers=headers)
    write = client.post("/write-check", headers=headers)

    assert me.status_code == 200, me.text
    assert me.json() == {"username": "read-key", "scope": "readonly"}
    assert write.status_code == 403
    assert write.json()["detail"] == "readonly key cannot write"


def test_last_used_at_is_populated_after_key_use(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    key_id, key = _create_api_key(conn)
    before = conn.execute("SELECT last_used_at FROM api_key WHERE id = ?", (key_id,)).fetchone()

    response = client.get("/api/auth/me", headers={"X-API-Key": key})
    after = conn.execute("SELECT last_used_at FROM api_key WHERE id = ?", (key_id,)).fetchone()

    assert before["last_used_at"] is None
    assert response.status_code == 200, response.text
    assert after["last_used_at"] is not None


def test_login_lockout_after_five_bad_attempts(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    _create_admin(conn, username="locked", password="secret")
    payload = {"username": "locked", "password": "wrong"}

    for _ in range(5):
        response = client.post("/api/auth/login", json=payload)
        assert response.status_code == 401, response.text

    locked = client.post("/api/auth/login", json=payload)

    assert locked.status_code == 429
    assert locked.json()["detail"] == "too many failed attempts, try again in a minute"


def test_auth_disabled_allows_me_without_credentials(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AUTH_DISABLED", "1")

    response = client.get("/api/auth/me")

    assert response.status_code == 200, response.text
    assert response.json() == {"username": "anonymous", "scope": "admin"}


def test_key_create_returns_plaintext_once_and_list_never_contains_it(
    client: TestClient, conn: sqlite3.Connection
) -> None:
    _create_admin(conn)
    _login(client)

    created = client.post("/api/keys", json={"label": "new-key", "scope": "admin"})
    listed = client.get("/api/keys")

    assert created.status_code == 201, created.text
    created_body: dict[str, Any] = created.json()
    assert created_body["key"].startswith("mcpp_")
    assert listed.status_code == 200, listed.text
    listed_body = listed.json()
    assert len(listed_body) == 1
    assert "key" not in listed_body[0]
    assert created_body["key"] not in str(listed_body)


def test_bootstrap_treats_blank_env_values_as_unset(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BOOTSTRAP_USER", "")
    monkeypatch.setenv("BOOTSTRAP_PASSWORD", "   ")

    auth.bootstrap(conn)
    row = conn.execute("SELECT username, password_hash FROM admin_user").fetchone()

    assert row["username"] == "admin"
    assert not auth.verify_password("", row["password_hash"])
    assert not auth.verify_password("   ", row["password_hash"])
