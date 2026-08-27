"""Tests for Phase 1: dataset relationship metadata."""

import sqlite3
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api
import auth
import db
import service
import store

# ------------------------------------------------------------------ fixtures


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.init_db(":memory:")
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def client(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("AUTH_DISABLED", "1")
    app = FastAPI()
    app.include_router(api.router)
    app.dependency_overrides[auth.get_conn] = lambda: conn
    with TestClient(app) as test_client:
        yield test_client


def _make_server(conn: sqlite3.Connection, slug: str = "s1") -> dict[str, Any]:
    return service.create_server(conn, slug, slug.upper(), "", "none")


def _make_dataset(
    conn: sqlite3.Connection,
    server_id: int,
    key: str,
    id_field: str,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return service.create_dataset(conn, server_id, key, id_field, rows)


def _rel(conn: sqlite3.Connection, server_id: int, **overrides: Any) -> dict[str, Any]:
    """Create a minimal relationship, with overrides applied."""
    svr = store.get_server(conn, server_id)
    assert svr is not None
    return service.create_relationship(conn, server_id, **overrides)


# ------------------------------------------------------------------ schema / migration


def test_fresh_db_has_relationship_table() -> None:
    conn = db.init_db(":memory:")
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dataset_relationship'"
    )
    assert cur.fetchone() is not None
    conn.close()


def test_v1_db_migrates_to_v2(tmp_path: Any) -> None:
    """Simulate a v1 database and confirm migrate() adds the new table."""
    path = str(tmp_path / "v1.db")
    # Build a v1 schema manually: only set version to 1, no relationship table
    raw = sqlite3.connect(path)
    raw.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '1');
        CREATE TABLE IF NOT EXISTS server (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            description TEXT,
            auth_mode TEXT NOT NULL DEFAULT 'none',
            custom_seed TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    raw.commit()
    raw.close()

    # Now open via db.init_db — migrate() must detect v1 < 2 and apply DDL
    conn = db.init_db(path)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='dataset_relationship'"
    )
    assert cur.fetchone() is not None, "dataset_relationship table missing after migration"
    conn.close()


# ------------------------------------------------------------------ CASCADE deletes


def test_delete_server_cascades_to_relationships(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "cascade-svr")
    src = _make_dataset(conn, svr["id"], "orders", "id", [{"id": 1}])
    tgt = _make_dataset(conn, svr["id"], "items", "item_id", [{"item_id": 1}])
    service.create_relationship(
        conn,
        svr["id"],
        name="r1",
        source_dataset_id=src["id"],
        source_field="id",
        target_dataset_id=tgt["id"],
        target_field="item_id",
        expand_name="item",
    )
    service.delete_server(conn, svr["id"])
    rows = store.list_relationships(conn, svr["id"])
    assert rows == []


def test_delete_source_dataset_cascades(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "cascade-src")
    src = _make_dataset(conn, svr["id"], "orders", "id", [{"id": 1}])
    tgt = _make_dataset(conn, svr["id"], "items", "item_id", [{"item_id": 1}])
    rel = service.create_relationship(
        conn,
        svr["id"],
        name="r1",
        source_dataset_id=src["id"],
        source_field="id",
        target_dataset_id=tgt["id"],
        target_field="item_id",
        expand_name="item",
    )
    service.delete_dataset(conn, src["id"])
    assert store.get_relationship(conn, rel["id"]) is None


def test_delete_target_dataset_cascades(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "cascade-tgt")
    src = _make_dataset(conn, svr["id"], "orders", "id", [{"id": 1}])
    tgt = _make_dataset(conn, svr["id"], "items", "item_id", [{"item_id": 1}])
    rel = service.create_relationship(
        conn,
        svr["id"],
        name="r1",
        source_dataset_id=src["id"],
        source_field="id",
        target_dataset_id=tgt["id"],
        target_field="item_id",
        expand_name="item",
    )
    service.delete_dataset(conn, tgt["id"])
    assert store.get_relationship(conn, rel["id"]) is None


# ------------------------------------------------------------------ UNIQUE constraints


def test_duplicate_name_in_server_rejected(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "u1")
    src = _make_dataset(conn, svr["id"], "a", "id", [{"id": 1}])
    tgt = _make_dataset(conn, svr["id"], "b", "id", [{"id": 1}])
    service.create_relationship(
        conn,
        svr["id"],
        name="rel",
        source_dataset_id=src["id"],
        source_field="id",
        target_dataset_id=tgt["id"],
        target_field="id",
        expand_name="b_ref",
    )
    with pytest.raises(service.ServiceError) as exc_info:
        service.create_relationship(
            conn,
            svr["id"],
            name="rel",
            source_dataset_id=src["id"],
            source_field="id",
            target_dataset_id=tgt["id"],
            target_field="id",
            expand_name="b_ref2",
        )
    assert exc_info.value.code == "conflict"


def test_duplicate_composite_key_rejected(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "u2")
    src = _make_dataset(conn, svr["id"], "a", "id", [{"id": 1}])
    tgt = _make_dataset(conn, svr["id"], "b", "id", [{"id": 1}])
    service.create_relationship(
        conn,
        svr["id"],
        name="rel1",
        source_dataset_id=src["id"],
        source_field="id",
        target_dataset_id=tgt["id"],
        target_field="id",
        expand_name="bx",
    )
    with pytest.raises(service.ServiceError) as exc_info:
        service.create_relationship(
            conn,
            svr["id"],
            name="rel2",
            source_dataset_id=src["id"],
            source_field="id",
            target_dataset_id=tgt["id"],
            target_field="id",
            expand_name="bx",  # same expand_name on same source_dataset
        )
    assert exc_info.value.code == "conflict"


# ------------------------------------------------------------------ cross-server rejection


def test_cross_server_source_rejected(conn: sqlite3.Connection) -> None:
    svr1 = _make_server(conn, "svr1")
    svr2 = _make_server(conn, "svr2")
    src = _make_dataset(conn, svr1["id"], "a", "id", [{"id": 1}])
    tgt = _make_dataset(conn, svr2["id"], "b", "id", [{"id": 1}])
    with pytest.raises(service.ServiceError):
        service.create_relationship(
            conn,
            svr2["id"],
            name="cross",
            source_dataset_id=src["id"],
            source_field="id",
            target_dataset_id=tgt["id"],
            target_field="id",
            expand_name="bref",
        )


def test_cross_server_target_rejected(conn: sqlite3.Connection) -> None:
    svr1 = _make_server(conn, "svr3")
    svr2 = _make_server(conn, "svr4")
    src = _make_dataset(conn, svr1["id"], "a", "id", [{"id": 1}])
    tgt = _make_dataset(conn, svr2["id"], "b", "id", [{"id": 1}])
    with pytest.raises(service.ServiceError):
        service.create_relationship(
            conn,
            svr1["id"],
            name="cross",
            source_dataset_id=src["id"],
            source_field="id",
            target_dataset_id=tgt["id"],
            target_field="id",
            expand_name="bref",
        )


# ------------------------------------------------------------------ expand name collisions


def test_duplicate_expand_on_same_source_rejected(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "ex1")
    src = _make_dataset(conn, svr["id"], "orders", "id", [{"id": 1}])
    tgt1 = _make_dataset(conn, svr["id"], "items", "item_id", [{"item_id": 1}])
    tgt2 = _make_dataset(conn, svr["id"], "products", "prod_id", [{"prod_id": 1}])
    service.create_relationship(
        conn,
        svr["id"],
        name="r1",
        source_dataset_id=src["id"],
        source_field="id",
        target_dataset_id=tgt1["id"],
        target_field="item_id",
        expand_name="thing",
    )
    with pytest.raises(service.ServiceError) as exc_info:
        service.create_relationship(
            conn,
            svr["id"],
            name="r2",
            source_dataset_id=src["id"],
            source_field="id",
            target_dataset_id=tgt2["id"],
            target_field="prod_id",
            expand_name="thing",
        )
    assert exc_info.value.code == "conflict"


def test_duplicate_inverse_on_same_target_rejected(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "ex2")
    src1 = _make_dataset(conn, svr["id"], "orders", "id", [{"id": 1}])
    src2 = _make_dataset(conn, svr["id"], "returns", "id", [{"id": 1}])
    tgt = _make_dataset(conn, svr["id"], "items", "item_id", [{"item_id": 1}])
    service.create_relationship(
        conn,
        svr["id"],
        name="r1",
        source_dataset_id=src1["id"],
        source_field="id",
        target_dataset_id=tgt["id"],
        target_field="item_id",
        expand_name="item1",
        inverse_expand_name="orders",
    )
    with pytest.raises(service.ServiceError) as exc_info:
        service.create_relationship(
            conn,
            svr["id"],
            name="r2",
            source_dataset_id=src2["id"],
            source_field="id",
            target_dataset_id=tgt["id"],
            target_field="item_id",
            expand_name="item2",
            inverse_expand_name="orders",
        )
    assert exc_info.value.code == "conflict"


# ------------------------------------------------------------------ reserved names


@pytest.mark.parametrize("reserved", ["expand", "limit", "offset", "_row_id"])
def test_reserved_expand_name_rejected(conn: sqlite3.Connection, reserved: str) -> None:
    svr = _make_server(conn, f"res-{reserved.strip('_').replace('_', '-')}")
    src = _make_dataset(conn, svr["id"], "a", "id", [{"id": 1}])
    tgt = _make_dataset(conn, svr["id"], "b", "id", [{"id": 1}])
    with pytest.raises(service.ServiceError):
        service.create_relationship(
            conn,
            svr["id"],
            name="r",
            source_dataset_id=src["id"],
            source_field="id",
            target_dataset_id=tgt["id"],
            target_field="id",
            expand_name=reserved,
        )


# ------------------------------------------------------------------ self-referencing


def test_self_reference_accepted(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "hr")
    employees = _make_dataset(
        conn,
        svr["id"],
        "employees",
        "employee_id",
        [
            {"employee_id": 1, "name": "Alice", "manager_id": None},
            {"employee_id": 2, "name": "Bob", "manager_id": 1},
            {"employee_id": 3, "name": "Carol", "manager_id": 1},
        ],
    )
    rel = service.create_relationship(
        conn,
        svr["id"],
        name="emp_manager",
        source_dataset_id=employees["id"],
        source_field="manager_id",
        target_dataset_id=employees["id"],
        target_field="employee_id",
        expand_name="manager",
        inverse_expand_name="direct_reports",
    )
    assert rel["source_dataset_id"] == employees["id"]
    assert rel["target_dataset_id"] == employees["id"]

    rows = store.list_rows(conn, employees["id"])
    expanded = service.expand_rows(conn, employees["id"], rows, ["manager"])
    # Alice has no manager
    alice = next(r for r in expanded if r["employee_id"] == 1)
    assert alice["manager"] is None
    # Bob's manager is Alice
    bob = next(r for r in expanded if r["employee_id"] == 2)
    assert bob["manager"]["employee_id"] == 1
    # _row_id stripped from expanded child
    assert "_row_id" not in bob["manager"]


# ------------------------------------------------------------------ parse_expand


def test_parse_expand_comma_string() -> None:
    assert service.parse_expand("manager,org_unit") == ["manager", "org_unit"]


def test_parse_expand_list() -> None:
    assert service.parse_expand(["manager", "org_unit"]) == ["manager", "org_unit"]


def test_parse_expand_none() -> None:
    assert service.parse_expand(None) == []


def test_parse_expand_deduplication() -> None:
    result = service.parse_expand("a,a,b")
    assert result == ["a", "b"]


def test_parse_expand_dot_rejected() -> None:
    with pytest.raises(service.ServiceError) as exc_info:
        service.parse_expand("manager.name")
    assert "dot" in exc_info.value.message.lower() or "depth" in exc_info.value.message.lower()


def test_parse_expand_more_than_five_rejected() -> None:
    with pytest.raises(service.ServiceError):
        service.parse_expand("a,b,c,d,e,f")


def test_parse_expand_empty_name_rejected() -> None:
    with pytest.raises(service.ServiceError):
        service.parse_expand("a,,b")


# ------------------------------------------------------------------ expand_rows


@pytest.fixture
def joined_conn() -> Iterator[SimpleNamespace]:
    """orders + order_lines with a relationship. Returns SimpleNamespace(conn, svr, orders, lines)."""  # noqa: E501
    connection = db.init_db(":memory:")
    svr = service.create_server(connection, "ej", "EJ", "", "none")
    orders = service.create_dataset(
        connection,
        svr["id"],
        "orders",
        "order_id",
        [{"order_id": 10, "status": "open"}, {"order_id": 20, "status": "closed"}],
    )
    lines = service.create_dataset(
        connection,
        svr["id"],
        "order_lines",
        "line_id",
        [
            {"line_id": 1, "order_id": 10, "product": "A"},
            {"line_id": 2, "order_id": 10, "product": "B"},
            {"line_id": 3, "order_id": 20, "product": "C"},
        ],
    )
    service.create_relationship(
        connection,
        svr["id"],
        name="lines_order",
        source_dataset_id=lines["id"],
        source_field="order_id",
        target_dataset_id=orders["id"],
        target_field="order_id",
        expand_name="order",
        inverse_expand_name="lines",
    )
    ctx = SimpleNamespace(conn=connection, svr=svr, orders=orders, lines=lines)
    try:
        yield ctx
    finally:
        connection.close()


def test_expand_forward_returns_object(joined_conn: SimpleNamespace) -> None:
    lines = store.list_rows(joined_conn.conn, joined_conn.lines["id"])
    expanded = service.expand_rows(joined_conn.conn, joined_conn.lines["id"], lines, ["order"])
    for row in expanded:
        assert "order" in row
        assert isinstance(row["order"], dict)
        assert row["order"]["order_id"] == row["order_id"]


def test_expand_forward_missing_target_is_none(joined_conn: SimpleNamespace) -> None:
    # add a row with a dangling reference
    orphan = [{"line_id": 99, "order_id": 999, "product": "X"}]
    store.add_rows(joined_conn.conn, joined_conn.lines["id"], orphan)
    lines_all = store.list_rows(joined_conn.conn, joined_conn.lines["id"])
    expanded = service.expand_rows(joined_conn.conn, joined_conn.lines["id"], lines_all, ["order"])
    orphan_row = next(r for r in expanded if r["line_id"] == 99)
    assert orphan_row["order"] is None


def test_expand_inverse_returns_list(joined_conn: SimpleNamespace) -> None:
    orders = store.list_rows(joined_conn.conn, joined_conn.orders["id"])
    expanded = service.expand_rows(joined_conn.conn, joined_conn.orders["id"], orders, ["lines"])
    order10 = next(r for r in expanded if r["order_id"] == 10)
    assert isinstance(order10["lines"], list)
    assert len(order10["lines"]) == 2


def test_expand_inverse_empty_list_when_none(joined_conn: SimpleNamespace) -> None:
    service.create_dataset(
        joined_conn.conn,
        joined_conn.svr["id"],
        "empty_orders",
        "order_id",
        [{"order_id": 30, "status": "pending"}],
    )
    # use the real orders dataset — order 20 has 1 line, order 10 has 2
    # but no order with id 999 would have 0 lines
    store.add_rows(
        joined_conn.conn, joined_conn.orders["id"], [{"order_id": 999, "status": "ghost"}]
    )
    orders = store.list_rows(joined_conn.conn, joined_conn.orders["id"])
    expanded = service.expand_rows(joined_conn.conn, joined_conn.orders["id"], orders, ["lines"])
    ghost = next(r for r in expanded if r["order_id"] == 999)
    assert ghost["lines"] == []


def test_expand_rows_int_string_coercion(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "coerce")
    orders = _make_dataset(conn, svr["id"], "orders", "order_id", [{"order_id": "10"}])
    lines = _make_dataset(
        conn,
        svr["id"],
        "order_lines",
        "line_id",
        [{"line_id": 1, "order_id": 10}],  # int vs string
    )
    service.create_relationship(
        conn,
        svr["id"],
        name="ol_order",
        source_dataset_id=lines["id"],
        source_field="order_id",
        target_dataset_id=orders["id"],
        target_field="order_id",
        expand_name="order",
    )
    lines_rows = store.list_rows(conn, lines["id"])
    expanded = service.expand_rows(conn, lines["id"], lines_rows, ["order"])
    assert expanded[0]["order"] is not None
    assert expanded[0]["order"]["order_id"] == "10"


def test_expand_rows_does_not_mutate_input(joined_conn: SimpleNamespace) -> None:
    lines = store.list_rows(joined_conn.conn, joined_conn.lines["id"])
    originals = [dict(r) for r in lines]
    service.expand_rows(joined_conn.conn, joined_conn.lines["id"], lines, ["order"])
    assert lines == originals


def test_expand_rows_row_id_on_base_stripped_from_children(joined_conn: SimpleNamespace) -> None:
    lines = store.list_rows(joined_conn.conn, joined_conn.lines["id"])
    assert all("_row_id" in r for r in lines)  # base rows have it
    expanded = service.expand_rows(joined_conn.conn, joined_conn.lines["id"], lines, ["order"])
    for row in expanded:
        assert "_row_id" in row  # preserved on base
        if row["order"] is not None:
            assert "_row_id" not in row["order"]  # stripped from child


def test_expand_rows_unknown_expand_raises(joined_conn: SimpleNamespace) -> None:
    lines = store.list_rows(joined_conn.conn, joined_conn.lines["id"])
    with pytest.raises(service.ServiceError):
        service.expand_rows(joined_conn.conn, joined_conn.lines["id"], lines, ["nonexistent"])


def test_expand_rows_empty_names_returns_unchanged(joined_conn: SimpleNamespace) -> None:
    lines = store.list_rows(joined_conn.conn, joined_conn.lines["id"])
    result = service.expand_rows(joined_conn.conn, joined_conn.lines["id"], lines, [])
    assert result is lines  # cheap path: exact same object


# ------------------------------------------------------------------ available_expands


def test_available_expands_both_directions(joined_conn: SimpleNamespace) -> None:
    lines_expands = service.available_expands(joined_conn.conn, joined_conn.lines["id"])
    orders_expands = service.available_expands(joined_conn.conn, joined_conn.orders["id"])

    forward = [e for e in lines_expands if e["direction"] == "forward"]
    assert any(e["name"] == "order" and e["returns"] == "object" for e in forward)

    inverse = [e for e in orders_expands if e["direction"] == "inverse"]
    assert any(e["name"] == "lines" and e["returns"] == "array" for e in inverse)


def test_available_expands_no_inverse_when_null(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "noninv")
    src = _make_dataset(conn, svr["id"], "a", "id", [{"id": 1}])
    tgt = _make_dataset(conn, svr["id"], "b", "id", [{"id": 1}])
    service.create_relationship(
        conn,
        svr["id"],
        name="r1",
        source_dataset_id=src["id"],
        source_field="id",
        target_dataset_id=tgt["id"],
        target_field="id",
        expand_name="bref",
        # no inverse_expand_name
    )
    tgt_expands = service.available_expands(conn, tgt["id"])
    assert not any(e["direction"] == "inverse" for e in tgt_expands)


# ------------------------------------------------------------------ clone


def test_clone_copies_relationships(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "orig")
    src = _make_dataset(conn, svr["id"], "orders", "id", [{"id": 1, "ref": 10}])
    tgt = _make_dataset(conn, svr["id"], "items", "item_id", [{"item_id": 10}])
    service.create_relationship(
        conn,
        svr["id"],
        name="r1",
        source_dataset_id=src["id"],
        source_field="ref",
        target_dataset_id=tgt["id"],
        target_field="item_id",
        expand_name="item",
    )
    cloned = service.clone_server(conn, svr["id"], "cloned", "Cloned")
    clone_rels = store.list_relationships(conn, cloned["id"])
    assert len(clone_rels) == 1

    # IDs must be different
    orig_rels = store.list_relationships(conn, svr["id"])
    assert clone_rels[0]["id"] != orig_rels[0]["id"]

    # Must point at cloned datasets, not source datasets
    cloned_datasets = {d["key"]: d["id"] for d in store.list_datasets(conn, cloned["id"])}
    assert clone_rels[0]["source_dataset_id"] == cloned_datasets["orders"]
    assert clone_rels[0]["target_dataset_id"] == cloned_datasets["items"]


# ------------------------------------------------------------------ ensure_demo_relationships


def _seed_orders_server(conn: sqlite3.Connection, slug: str = "demo-orders") -> dict[str, Any]:
    svr = service.create_server(conn, slug, "Demo Orders", "", "none")
    service.create_dataset(
        conn,
        svr["id"],
        "orders",
        "id",
        [{"id": 1}, {"id": 2}],
    )
    service.create_dataset(
        conn,
        svr["id"],
        "order_lines",
        "id",
        [{"id": 1, "order_id": 1}, {"id": 2, "order_id": 2}],
    )
    return svr


def test_ensure_demo_relationships_creates_for_orders(conn: sqlite3.Connection) -> None:
    svr = _seed_orders_server(conn)
    result = service.ensure_demo_relationships(conn, svr["id"])
    assert result["relationships_created"] > 0


def test_ensure_demo_relationships_is_idempotent(conn: sqlite3.Connection) -> None:
    svr = _seed_orders_server(conn)
    service.ensure_demo_relationships(conn, svr["id"])
    result2 = service.ensure_demo_relationships(conn, svr["id"])
    assert result2["relationships_created"] == 0
    assert result2["skipped_existing"] > 0


# ------------------------------------------------------------------ validate_relationships


def test_validate_relationships_returns_report(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "vr1")
    src = _make_dataset(conn, svr["id"], "orders", "id", [{"id": 1, "ref": 10}])
    tgt = _make_dataset(conn, svr["id"], "items", "item_id", [{"item_id": 99}])
    service.create_relationship(
        conn,
        svr["id"],
        name="r1",
        source_dataset_id=src["id"],
        source_field="ref",
        target_dataset_id=tgt["id"],
        target_field="item_id",
        expand_name="item",
    )
    report = service.validate_relationships(conn, svr["id"])
    assert "ok" in report
    assert "relationship_count" in report
    assert "issues" in report
    assert isinstance(report["issues"], list)
    # orphan warning: ref=10 but item_id=99
    warnings = [i for i in report["issues"] if i["severity"] == "warning"]
    assert len(warnings) > 0


def test_validate_relationships_no_raise_on_orphans(conn: sqlite3.Connection) -> None:
    svr = _make_server(conn, "vr2")
    src = _make_dataset(conn, svr["id"], "a", "id", [{"id": 1, "ref": 999}])
    tgt = _make_dataset(conn, svr["id"], "b", "id", [{"id": 1}])
    service.create_relationship(
        conn,
        svr["id"],
        name="r1",
        source_dataset_id=src["id"],
        source_field="ref",
        target_dataset_id=tgt["id"],
        target_field="id",
        expand_name="bref",
    )
    # Must not raise
    report = service.validate_relationships(conn, svr["id"])
    assert report["ok"] is False or len(report["issues"]) > 0


# ------------------------------------------------------------------ API routes


def _post_server_via_api(client: TestClient, slug: str = "api-svr") -> dict[str, Any]:
    r = client.post("/api/servers", json={"slug": slug, "name": slug})
    assert r.status_code == 201, r.text
    return r.json()


def _post_dataset_via_api(
    client: TestClient, server_id: int, key: str, id_field: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    r = client.post(
        f"/api/servers/{server_id}/datasets",
        json={"key": key, "id_field": id_field, "rows": rows},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_api_create_and_list_relationships(client: TestClient) -> None:
    svr = _post_server_via_api(client, "api-rel")
    src = _post_dataset_via_api(client, svr["id"], "orders", "id", [{"id": 1}])
    tgt = _post_dataset_via_api(client, svr["id"], "items", "item_id", [{"item_id": 1}])

    r = client.post(
        f"/api/servers/{svr['id']}/relationships",
        json={
            "name": "r1",
            "source_dataset_id": src["id"],
            "source_field": "id",
            "target_dataset_id": tgt["id"],
            "target_field": "item_id",
            "expand_name": "item",
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["expand_name"] == "item"

    r2 = client.get(f"/api/servers/{svr['id']}/relationships")
    assert r2.status_code == 200
    assert len(r2.json()) == 1


def test_api_delete_relationship(client: TestClient) -> None:
    svr = _post_server_via_api(client, "api-del")
    src = _post_dataset_via_api(client, svr["id"], "orders", "id", [{"id": 1}])
    tgt = _post_dataset_via_api(client, svr["id"], "items", "item_id", [{"item_id": 1}])
    r = client.post(
        f"/api/servers/{svr['id']}/relationships",
        json={
            "name": "r1",
            "source_dataset_id": src["id"],
            "source_field": "id",
            "target_dataset_id": tgt["id"],
            "target_field": "item_id",
            "expand_name": "item",
        },
    )
    rel_id = r.json()["id"]
    del_r = client.delete(f"/api/relationships/{rel_id}")
    assert del_r.status_code == 204

    r2 = client.get(f"/api/servers/{svr['id']}/relationships")
    assert r2.json() == []


def test_api_validate_route(client: TestClient) -> None:
    svr = _post_server_via_api(client, "api-val")
    _post_dataset_via_api(client, svr["id"], "orders", "id", [{"id": 1}])
    r = client.get(f"/api/servers/{svr['id']}/relationships/validate")
    assert r.status_code == 200
    body = r.json()
    assert "ok" in body
    assert "relationship_count" in body


def test_api_ensure_demo_route(client: TestClient) -> None:
    # Seed orders-like server manually
    svr = _post_server_via_api(client, "api-demo")
    _post_dataset_via_api(client, svr["id"], "orders", "id", [{"id": 1}])
    _post_dataset_via_api(client, svr["id"], "order_lines", "id", [{"id": 1, "order_id": 1}])
    r = client.post("/api/relationships/ensure-demo", json={"server_id": svr["id"]})
    assert r.status_code == 200
    body = r.json()
    assert "relationships_created" in body


def test_api_available_expands(client: TestClient) -> None:
    svr = _post_server_via_api(client, "api-exp")
    src = _post_dataset_via_api(client, svr["id"], "orders", "id", [{"id": 1}])
    tgt = _post_dataset_via_api(client, svr["id"], "items", "item_id", [{"item_id": 1}])
    client.post(
        f"/api/servers/{svr['id']}/relationships",
        json={
            "name": "r1",
            "source_dataset_id": src["id"],
            "source_field": "id",
            "target_dataset_id": tgt["id"],
            "target_field": "item_id",
            "expand_name": "item",
            "inverse_expand_name": "orders",
        },
    )
    r = client.get(f"/api/datasets/{src['id']}/expands")
    assert r.status_code == 200
    expands = r.json()
    assert any(e["name"] == "item" and e["direction"] == "forward" for e in expands)
