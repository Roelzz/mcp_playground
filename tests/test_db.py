import sqlite3

import pytest

import db
import store


@pytest.fixture
def conn(tmp_path):
    c = db.init_db(str(tmp_path / "test.db"))
    yield c
    c.close()


def test_fresh_db_creates_schema(tmp_path):
    c = db.init_db(str(tmp_path / "fresh.db"))
    tables = {
        r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {
        "schema_version",
        "admin_user",
        "api_key",
        "server",
        "dataset",
        "dataset_row",
        "dataset_seed_row",
        "endpoint",
        "llm_endpoint",
        "llm_response",
        "call_log",
    } <= tables
    assert db.current_version(c) == db.SCHEMA_VERSION
    c.close()


def test_migration_is_idempotent_across_boots(tmp_path):
    path = str(tmp_path / "boot.db")
    c1 = db.init_db(path)
    sid = store.create_server(c1, "acme", "Acme", "desc", "none")
    c1.close()

    c2 = db.init_db(path)
    assert db.current_version(c2) == db.SCHEMA_VERSION
    assert store.get_server(c2, sid)["slug"] == "acme"
    assert c2.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()["n"] == 1
    c2.close()


def test_wal_and_foreign_keys_enabled(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_unique_slug_enforced(conn):
    store.create_server(conn, "acme", "Acme", "", "none")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_server(conn, "acme", "Other", "", "none")


def test_unique_tool_name_per_server(conn):
    sid = store.create_server(conn, "acme", "Acme", "", "none")
    did = store.create_dataset(conn, sid, "orders", "id")
    store.create_endpoint(conn, sid, "/orders", "GET", "list_orders", "", did, [])
    with pytest.raises(sqlite3.IntegrityError):
        store.create_endpoint(conn, sid, "/other", "GET", "list_orders", "", did, [])

    sid2 = store.create_server(conn, "beta", "Beta", "", "none")
    did2 = store.create_dataset(conn, sid2, "orders", "id")
    store.create_endpoint(conn, sid2, "/orders", "GET", "list_orders", "", did2, [])


def test_delete_server_cascades(conn):
    sid = store.create_server(conn, "acme", "Acme", "", "none")
    did = store.create_dataset(conn, sid, "orders", "id")
    store.add_rows(conn, did, [{"id": 1}, {"id": 2}])
    store.replace_seed_rows(conn, did, [{"id": 1}])
    store.create_endpoint(conn, sid, "/orders", "GET", "list_orders", "", did, [])

    store.delete_server(conn, sid)

    assert conn.execute("SELECT COUNT(*) AS n FROM dataset").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM dataset_row").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM dataset_seed_row").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM endpoint").fetchone()["n"] == 0


def test_rows_roundtrip_json(conn):
    sid = store.create_server(conn, "acme", "Acme", "", "none")
    did = store.create_dataset(conn, sid, "orders", "id")
    store.add_rows(conn, did, [{"id": 1, "total": 9.5, "open": True, "tags": ["a"]}])
    rows = store.list_rows(conn, did)
    assert rows[0]["total"] == 9.5
    assert rows[0]["open"] is True
    assert rows[0]["tags"] == ["a"]
    assert "_row_id" in rows[0]


def test_reset_to_seed_restores_baseline(conn):
    sid = store.create_server(conn, "acme", "Acme", "", "none")
    did = store.create_dataset(conn, sid, "orders", "id")
    seed = [{"id": 1, "s": "a"}, {"id": 2, "s": "b"}]
    store.add_rows(conn, did, seed)
    store.save_as_seed(conn, did)

    store.replace_rows(conn, did, [{"id": 99, "s": "mutated"}])
    assert store.count_rows(conn, did) == 1

    n = store.reset_to_seed(conn, did)
    assert n == 2
    live = [{k: v for k, v in r.items() if k != "_row_id"} for r in store.list_rows(conn, did)]
    assert live == seed


def test_save_as_seed_overwrites_previous_seed(conn):
    sid = store.create_server(conn, "acme", "Acme", "", "none")
    did = store.create_dataset(conn, sid, "orders", "id")
    store.replace_seed_rows(conn, did, [{"id": 1}])
    store.replace_rows(conn, did, [{"id": 7}, {"id": 8}])
    assert store.save_as_seed(conn, did) == 2
    assert store.list_seed_rows(conn, did) == [{"id": 7}, {"id": 8}]


def test_reset_to_seed_with_empty_seed_clears_live(conn):
    sid = store.create_server(conn, "acme", "Acme", "", "none")
    did = store.create_dataset(conn, sid, "orders", "id")
    store.add_rows(conn, did, [{"id": 1}])
    assert store.reset_to_seed(conn, did) == 0


def test_endpoint_summary_fields_roundtrip(conn):
    sid = store.create_server(conn, "acme", "Acme", "", "none")
    did = store.create_dataset(conn, sid, "orders", "id")
    eid = store.create_endpoint(conn, sid, "/orders", "GET", "list_orders", "d", did, ["id", "s"])
    assert store.get_endpoint(conn, eid)["summary_fields"] == ["id", "s"]
    store.update_endpoint(conn, eid, summary_fields=["id"])
    assert store.get_endpoint(conn, eid)["summary_fields"] == ["id"]
    assert store.list_endpoints(conn, sid)[0]["summary_fields"] == ["id"]


def test_update_server_renames(conn):
    sid = store.create_server(conn, "acme", "Acme", "", "none")
    store.update_server(conn, sid, name="Renamed")
    assert store.get_server(conn, sid)["name"] == "Renamed"


def test_update_ignores_unknown_fields(conn):
    sid = store.create_server(conn, "acme", "Acme", "", "none")
    store.update_server(conn, sid, bogus="x")
    assert store.get_server(conn, sid)["slug"] == "acme"


def test_auth_mode_check_constraint(conn):
    with pytest.raises(sqlite3.IntegrityError):
        store.create_server(conn, "acme", "Acme", "", "oauth")


def test_llm_endpoint_and_responses(conn):
    lid = store.create_llm_endpoint(
        conn, slug="mock-gpt", name="Mock GPT", description="", mode="mock", model_name="m1"
    )
    store.set_llm_responses(
        conn,
        lid,
        [
            {"match_type": "contains", "match_value": "hello", "response": "hi"},
            {"match_type": "always", "response": "fallback"},
        ],
    )
    rs = store.list_llm_responses(conn, lid)
    assert [r["ordinal"] for r in rs] == [0, 1]
    assert rs[0]["match_value"] == "hello"

    store.set_llm_responses(conn, lid, [{"match_type": "always", "response": "only"}])
    assert len(store.list_llm_responses(conn, lid)) == 1

    store.delete_llm_endpoint(conn, lid)
    assert conn.execute("SELECT COUNT(*) AS n FROM llm_response").fetchone()["n"] == 0


def test_llm_mode_check_constraint(conn):
    with pytest.raises(sqlite3.IntegrityError):
        store.create_llm_endpoint(conn, slug="x", name="X", mode="passthrough")


def test_call_log_and_traffic(conn):
    store.log_call(
        conn,
        "mcp",
        "ok",
        target_slug="acme",
        tool_name="list_orders",
        request={"a": 1},
        response={"b": 2},
        duration_ms=12,
        actor="key:demo",
    )
    store.log_call(conn, "mcp", "error", target_slug="beta")
    all_traffic = store.get_traffic(conn)
    assert len(all_traffic) == 2
    assert all_traffic[0]["target_slug"] == "beta"
    acme = store.get_traffic(conn, target_slug="acme")
    assert len(acme) == 1
    assert acme[0]["request"] == {"a": 1}
    store.clear_traffic(conn)
    assert store.get_traffic(conn) == []


def test_api_key_lifecycle(conn):
    kid = store.create_api_key(conn, "demo", "hash123", "admin")
    assert store.get_api_key_by_hash(conn, "hash123")["scope"] == "admin"
    store.touch_api_key(conn, kid)
    assert store.get_api_key_by_hash(conn, "hash123")["last_used_at"] is not None
    store.revoke_api_key(conn, kid)
    assert store.get_api_key_by_hash(conn, "hash123") is None
    assert len(store.list_api_keys(conn)) == 1


def test_admin_user(conn):
    assert store.count_admin_users(conn) == 0
    store.create_admin_user(conn, "admin", "hashed")
    assert store.count_admin_users(conn) == 1
    assert store.get_admin_user(conn, "admin")["password_hash"] == "hashed"
    assert store.get_admin_user(conn, "nope") is None


def test_dataset_unique_key_per_server(conn):
    sid = store.create_server(conn, "acme", "Acme", "", "none")
    store.create_dataset(conn, sid, "orders", "id")
    with pytest.raises(sqlite3.IntegrityError):
        store.create_dataset(conn, sid, "orders", "id")


def test_delete_and_update_row(conn):
    sid = store.create_server(conn, "acme", "Acme", "", "none")
    did = store.create_dataset(conn, sid, "orders", "id")
    store.add_rows(conn, did, [{"id": 1, "s": "a"}])
    row = store.list_rows(conn, did)[0]
    store.update_row(conn, row["_row_id"], {"id": 1, "s": "b"})
    assert store.list_rows(conn, did)[0]["s"] == "b"
    store.delete_row(conn, row["_row_id"])
    assert store.count_rows(conn, did) == 0
