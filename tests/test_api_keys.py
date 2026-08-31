import sqlite3
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import db
import service


@pytest.fixture
def conn(tmp_path) -> Iterator[sqlite3.Connection]:
    connection = db.init_db(str(tmp_path / "api-keys.db"))
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def client(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("AUTH_DISABLED", "1")
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[auth.get_conn] = lambda: conn
    with TestClient(app) as test_client:
        yield test_client


def test_create_returns_plaintext_key_but_list_omits_it(conn: sqlite3.Connection) -> None:
    created = auth.create_api_key(conn, "trainer-key")
    listed = auth.list_api_keys(conn)

    assert created["key"].startswith("mcpp_")
    assert len(listed) == 1
    assert "key" not in listed[0]
    assert created["key"] not in str(listed)


def test_list_api_keys_never_contains_key_hash(conn: sqlite3.Connection) -> None:
    auth.create_api_key(conn, "trainer-key")

    listed = auth.list_api_keys(conn)

    assert "key_hash" not in listed[0]


@pytest.mark.parametrize("label", ["", "   "])
def test_create_api_key_rejects_empty_label(conn: sqlite3.Connection, label: str) -> None:
    with pytest.raises(service.ServiceError, match="label must not be empty"):
        auth.create_api_key(conn, label)


def test_create_api_key_rejects_invalid_scope(conn: sqlite3.Connection) -> None:
    with pytest.raises(service.ServiceError, match="scope must be 'admin' or 'readonly'"):
        auth.create_api_key(conn, "trainer-key", scope="bogus")


def test_create_api_key_readonly_scope_round_trips(conn: sqlite3.Connection) -> None:
    created = auth.create_api_key(conn, "readonly-key", scope="readonly")
    listed = auth.list_api_keys(conn)

    assert created["scope"] == "readonly"
    assert listed[0]["scope"] == "readonly"


def test_revoke_api_key_returns_true_and_removes_key(conn: sqlite3.Connection) -> None:
    created = auth.create_api_key(conn, "trainer-key")

    revoked = auth.revoke_api_key(conn, int(created["id"]))

    assert revoked is True
    assert auth.list_api_keys(conn) == []


def test_revoke_api_key_nonexistent_returns_false(conn: sqlite3.Connection) -> None:
    assert auth.revoke_api_key(conn, 9999) is False


def test_revoke_api_key_twice_returns_true_then_false(conn: sqlite3.Connection) -> None:
    created = auth.create_api_key(conn, "trainer-key")

    first = auth.revoke_api_key(conn, int(created["id"]))
    second = auth.revoke_api_key(conn, int(created["id"]))

    assert first is True
    assert second is False


def test_http_api_key_routes_work_end_to_end(client: TestClient) -> None:
    created = client.post("/api/keys", json={"label": "http-key", "scope": "admin"})
    listed = client.get("/api/keys")
    created_body: dict[str, Any] = created.json()
    deleted = client.delete(f"/api/keys/{created_body['id']}")
    listed_after_delete = client.get("/api/keys")

    assert created.status_code == 201, created.text
    assert created_body["key"].startswith("mcpp_")
    assert listed.status_code == 200, listed.text
    assert listed.json() == [
        {
            "id": created_body["id"],
            "label": "http-key",
            "scope": "admin",
            "created_at": created_body["created_at"],
            "last_used_at": None,
        }
    ]
    assert deleted.status_code == 204, deleted.text
    assert listed_after_delete.status_code == 200, listed_after_delete.text
    assert listed_after_delete.json() == []
