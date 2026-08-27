import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import db
import store
from executor import (
    ExecutorError,
    derive_field_schema,
    execute,
    infer_tool_type,
    path_param_name,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    c = db.init_db(str(tmp_path / "test.db"))
    yield c
    c.close()


@pytest.fixture
def playground(conn: sqlite3.Connection) -> dict[str, Any]:
    server_id = store.create_server(conn, "acme", "Acme", "", "none")
    dataset_id = store.create_dataset(conn, server_id, "orders", "id")
    rows = [
        {
            "id": 1,
            "name": "Alpha Order",
            "status": "open",
            "customer": "Acme",
            "priority": 1,
            "active": True,
            "total": 9.5,
        },
        {
            "id": 2,
            "name": "Beta Order",
            "status": "closed",
            "customer": "Beta",
            "priority": 2,
            "active": False,
            "total": 11.0,
        },
        {
            "id": 3,
            "name": "Gamma Widget",
            "status": "open",
            "customer": "Acme",
            "priority": 1,
            "active": True,
            "total": 15.25,
            "notes": None,
        },
        {
            "id": 4,
            "name": "delta shipment",
            "status": "pending",
            "customer": "Delta",
            "priority": 3,
            "active": False,
            "total": 4.0,
        },
        {
            "id": 5,
            "name": "Echo Order",
            "status": "open",
            "customer": "Echo",
            "priority": 2,
            "active": True,
            "total": 8.75,
        },
    ]
    store.add_rows(conn, dataset_id, rows)
    endpoints = {
        "list": _create_endpoint(conn, server_id, dataset_id, "/orders", "GET", "list_orders"),
        "summary": _create_endpoint(
            conn,
            server_id,
            dataset_id,
            "/orders",
            "GET",
            "summarize_orders",
            ["id", "name"],
        ),
        "search": _create_endpoint(
            conn, server_id, dataset_id, "/orders/search", "GET", "search_orders"
        ),
        "get": _create_endpoint(conn, server_id, dataset_id, "/orders/{id}", "GET", "get_order"),
        "create": _create_endpoint(conn, server_id, dataset_id, "/orders", "POST", "create_order"),
        "update": _create_endpoint(
            conn, server_id, dataset_id, "/orders/{id}", "PUT", "update_order"
        ),
        "delete": _create_endpoint(
            conn, server_id, dataset_id, "/orders/{id}", "DELETE", "delete_order"
        ),
    }
    dataset = store.get_dataset(conn, dataset_id)
    assert dataset is not None
    return {"server_id": server_id, "dataset": dataset, "endpoints": endpoints}


def _create_endpoint(
    conn: sqlite3.Connection,
    server_id: int,
    dataset_id: int,
    path: str,
    method: str,
    tool_name: str,
    summary_fields: list[str] | None = None,
) -> dict[str, Any]:
    endpoint_id = store.create_endpoint(
        conn, server_id, path, method, tool_name, "", dataset_id, summary_fields or []
    )
    endpoint = store.get_endpoint(conn, endpoint_id)
    assert endpoint is not None
    return endpoint


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/orders", "list"),
        ("GET", "/orders/search", "search"),
        ("GET", "/orders/{id}", "get"),
        ("POST", "/orders", "create"),
        ("PUT", "/orders/{id}", "update"),
        ("PATCH", "/orders/{id}", "update"),
        ("DELETE", "/orders/{id}", "delete"),
        ("get", "/orders/", "list"),
    ],
)
def test_infer_tool_type_happy_shapes(method: str, path: str, expected: str) -> None:
    assert infer_tool_type(method, path) == expected


def test_infer_tool_type_search_wins_over_get() -> None:
    assert infer_tool_type("GET", "/orders/search") == "search"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/orders"),
        ("DELETE", "/orders"),
        ("POST", "/orders/{id}"),
        ("HEAD", "/orders"),
        ("GET", "/a/b/c"),
    ],
)
def test_infer_tool_type_raises_on_invalid_shapes(method: str, path: str) -> None:
    with pytest.raises(ExecutorError) as exc:
        infer_tool_type(method, path)
    assert method in str(exc.value)
    assert path in str(exc.value)


@pytest.mark.parametrize(
    ("path", "expected"),
    [("/orders/{order_id}", "order_id"), ("/orders", None), ("/orders/search", None)],
)
def test_path_param_name(path: str, expected: str | None) -> None:
    assert path_param_name(path) == expected


def test_derive_field_schema_unions_heterogeneous_rows() -> None:
    rows = [
        {"_row_id": 1, "id": 1, "active": True, "empty": None},
        {"name": "Alpha", "score": 9.5, "empty": None, "payload": {"x": 1}},
        {"tags": ["a"], "active": 1},
    ]

    assert derive_field_schema(rows) == {
        "id": "integer",
        "active": "boolean",
        "empty": "string",
        "name": "string",
        "score": "number",
        "payload": "string",
        "tags": "string",
    }


def test_list_returns_all_rows_without_row_id(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    rows = execute(conn, playground["endpoints"]["list"], playground["dataset"], {})

    assert len(rows) == 5
    assert all("_row_id" not in row for row in rows)


def test_list_equality_filter_narrows_results(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    rows = execute(conn, playground["endpoints"]["list"], playground["dataset"], {"status": "open"})

    assert [row["id"] for row in rows] == [1, 3, 5]


def test_list_string_filter_value_matches_int_stored_field(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    rows = execute(conn, playground["endpoints"]["list"], playground["dataset"], {"priority": "1"})

    assert [row["id"] for row in rows] == [1, 3]


def test_list_limit_caps_results(playground: dict[str, Any], conn: sqlite3.Connection) -> None:
    rows = execute(conn, playground["endpoints"]["list"], playground["dataset"], {"limit": 2})

    assert [row["id"] for row in rows] == [1, 2]


def test_list_summary_fields_projects_exact_keys(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    rows = execute(conn, playground["endpoints"]["summary"], playground["dataset"], {"limit": 1})

    assert rows == [{"id": 1, "name": "Alpha Order"}]


def test_search_q_matches_case_insensitively_on_string_field(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    rows = execute(conn, playground["endpoints"]["search"], playground["dataset"], {"q": "gamma"})

    assert rows == [
        {
            "id": 3,
            "name": "Gamma Widget",
            "status": "open",
            "customer": "Acme",
            "priority": 1,
            "active": True,
            "total": 15.25,
            "notes": None,
        }
    ]


def test_search_q_no_match_returns_empty(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    rows = execute(conn, playground["endpoints"]["search"], playground["dataset"], {"q": "xyz"})

    assert rows == []


def test_search_q_combined_with_equality_filter(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    rows = execute(
        conn,
        playground["endpoints"]["search"],
        playground["dataset"],
        {"q": "order", "status": "open"},
    )

    assert [row["id"] for row in rows] == [1, 5]


def test_get_returns_row(playground: dict[str, Any], conn: sqlite3.Connection) -> None:
    row = execute(conn, playground["endpoints"]["get"], playground["dataset"], {"id": 2})

    assert row["name"] == "Beta Order"
    assert "_row_id" not in row


def test_get_coerces_string_id_to_int(playground: dict[str, Any], conn: sqlite3.Connection) -> None:
    row = execute(conn, playground["endpoints"]["get"], playground["dataset"], {"id": "3"})

    assert row["id"] == 3


def test_get_unknown_id_raises_not_found(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    with pytest.raises(ExecutorError) as exc:
        execute(conn, playground["endpoints"]["get"], playground["dataset"], {"id": 999})

    assert exc.value.code == "not_found"


def test_create_inserts_and_auto_assigns_max_plus_one(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    row = execute(
        conn,
        playground["endpoints"]["create"],
        playground["dataset"],
        {"name": "Foxtrot", "status": "open"},
    )

    assert row == {"name": "Foxtrot", "status": "open", "id": 6}
    assert store.count_rows(conn, playground["dataset"]["id"]) == 6


def test_create_auto_assigns_id_one_into_empty_dataset(conn: sqlite3.Connection) -> None:
    server_id = store.create_server(conn, "empty", "Empty", "", "none")
    dataset_id = store.create_dataset(conn, server_id, "things", "id")
    dataset = store.get_dataset(conn, dataset_id)
    endpoint = _create_endpoint(conn, server_id, dataset_id, "/things", "POST", "create_thing")
    assert dataset is not None

    row = execute(conn, endpoint, dataset, {"name": "First"})

    assert row == {"name": "First", "id": 1}


def test_create_honours_explicit_id(playground: dict[str, Any], conn: sqlite3.Connection) -> None:
    row = execute(
        conn,
        playground["endpoints"]["create"],
        playground["dataset"],
        {"id": 99, "name": "Manual"},
    )

    assert row == {"id": 99, "name": "Manual"}


def test_create_visible_to_list_endpoint_on_same_dataset(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    execute(
        conn,
        playground["endpoints"]["create"],
        playground["dataset"],
        {"id": 42, "name": "Shared", "status": "new"},
    )

    rows = execute(conn, playground["endpoints"]["list"], playground["dataset"], {"id": "42"})

    assert rows == [{"id": 42, "name": "Shared", "status": "new"}]


def test_update_merges_body_fields_and_keeps_untouched_fields(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    row = execute(
        conn,
        playground["endpoints"]["update"],
        playground["dataset"],
        {"id": 2, "status": "archived"},
    )

    assert row["id"] == 2
    assert row["name"] == "Beta Order"
    assert row["status"] == "archived"


def test_update_does_not_leak_path_param_into_row(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    execute(
        conn,
        playground["endpoints"]["update"],
        playground["dataset"],
        {"id": "3", "status": "reviewed"},
    )

    row = execute(conn, playground["endpoints"]["get"], playground["dataset"], {"id": 3})
    assert row["id"] == 3
    assert isinstance(row["id"], int)
    assert row["status"] == "reviewed"


def test_update_unknown_id_raises_not_found(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    with pytest.raises(ExecutorError) as exc:
        execute(
            conn,
            playground["endpoints"]["update"],
            playground["dataset"],
            {"id": 999, "status": "missing"},
        )

    assert exc.value.code == "not_found"


def test_delete_removes_row_and_returns_deleted_shape(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    result = execute(conn, playground["endpoints"]["delete"], playground["dataset"], {"id": "4"})

    assert result == {"deleted": True, "id": 4}
    assert store.count_rows(conn, playground["dataset"]["id"]) == 4
    with pytest.raises(ExecutorError) as exc:
        execute(conn, playground["endpoints"]["get"], playground["dataset"], {"id": 4})
    assert exc.value.code == "not_found"


def test_delete_unknown_id_raises_not_found(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    with pytest.raises(ExecutorError) as exc:
        execute(conn, playground["endpoints"]["delete"], playground["dataset"], {"id": 999})

    assert exc.value.code == "not_found"


def test_create_rejects_unknown_field_on_populated_dataset(
    playground: dict[str, Any], conn: sqlite3.Connection
) -> None:
    with pytest.raises(ExecutorError) as excinfo:
        execute(
            conn,
            playground["endpoints"]["create"],
            playground["dataset"],
            {"name": "Typo", "stattus": "open"},
        )

    assert excinfo.value.code == "invalid_params"
    assert "stattus" in str(excinfo.value)
    assert store.count_rows(conn, playground["dataset"]["id"]) == 5


def test_create_accepts_any_field_on_empty_dataset(conn: sqlite3.Connection) -> None:
    server_id = store.create_server(conn, "empty", "Empty", "", "none")
    dataset_id = store.create_dataset(conn, server_id, "things", "id")
    dataset = store.get_dataset(conn, dataset_id)
    endpoint = _create_endpoint(conn, server_id, dataset_id, "/things", "POST", "create_thing")
    assert dataset is not None

    row = execute(conn, endpoint, dataset, {"whatever": "goes", "shape": "free"})

    assert row == {"whatever": "goes", "shape": "free", "id": 1}


def test_update_rejects_unknown_field(playground: dict[str, Any], conn: sqlite3.Connection) -> None:
    with pytest.raises(ExecutorError) as excinfo:
        execute(
            conn,
            playground["endpoints"]["update"],
            playground["dataset"],
            {"id": "1", "stattus": "closed"},
        )

    assert excinfo.value.code == "invalid_params"
    assert "stattus" in str(excinfo.value)


# ---------------------------------------------------------------------------
# expand tests
# ---------------------------------------------------------------------------

import service  # noqa: E402


@pytest.fixture
def expand_pg(conn: sqlite3.Connection) -> dict[str, Any]:
    """Two datasets (orders + order_lines) with a many-to-one relationship."""
    server_id = store.create_server(conn, "exp-test", "ExpandTest", "", "none")

    orders_ds_id = store.create_dataset(conn, server_id, "orders", "id")
    store.add_rows(
        conn,
        orders_ds_id,
        [
            {"id": 10, "name": "Order Ten"},
            {"id": 20, "name": "Order Twenty"},
        ],
    )

    lines_ds_id = store.create_dataset(conn, server_id, "order_lines", "id")
    store.add_rows(
        conn,
        lines_ds_id,
        [
            {"id": 1, "order_id": 10, "qty": 2},
            {"id": 2, "order_id": 10, "qty": 5},
            {"id": 3, "order_id": 20, "qty": 1},
        ],
    )

    store.create_relationship(
        conn,
        server_id,
        name="lines_to_order",
        source_dataset_id=lines_ds_id,
        source_field="order_id",
        target_dataset_id=orders_ds_id,
        target_field="id",
        relation_type="many_to_one",
        expand_name="order",
        inverse_expand_name="lines",
    )

    orders_ds = store.get_dataset(conn, orders_ds_id)
    lines_ds = store.get_dataset(conn, lines_ds_id)
    assert orders_ds is not None and lines_ds is not None

    list_orders_ep = _create_endpoint(
        conn, server_id, orders_ds_id, "/orders", "GET", "list_orders"
    )
    list_orders_summary_ep = _create_endpoint(
        conn, server_id, orders_ds_id, "/orders", "GET", "summarize_orders", ["id", "name"]
    )
    get_order_ep = _create_endpoint(
        conn, server_id, orders_ds_id, "/orders/{id}", "GET", "get_order"
    )
    list_lines_ep = _create_endpoint(
        conn, server_id, lines_ds_id, "/order_lines", "GET", "list_order_lines"
    )
    create_order_ep = _create_endpoint(
        conn, server_id, orders_ds_id, "/orders", "POST", "create_order"
    )
    update_order_ep = _create_endpoint(
        conn, server_id, orders_ds_id, "/orders/{id}", "PUT", "update_order"
    )
    delete_order_ep = _create_endpoint(
        conn, server_id, orders_ds_id, "/orders/{id}", "DELETE", "delete_order"
    )

    return {
        "server_id": server_id,
        "orders_ds": orders_ds,
        "lines_ds": lines_ds,
        "ep_list_orders": list_orders_ep,
        "ep_list_orders_summary": list_orders_summary_ep,
        "ep_get_order": get_order_ep,
        "ep_list_lines": list_lines_ep,
        "ep_create_order": create_order_ep,
        "ep_update_order": update_order_ep,
        "ep_delete_order": delete_order_ep,
    }


def test_list_expand_inverse_returns_array(
    expand_pg: dict[str, Any], conn: sqlite3.Connection
) -> None:
    rows = execute(
        conn, expand_pg["ep_list_orders"], expand_pg["orders_ds"], {"expand": "lines"}
    )
    assert len(rows) == 2
    assert isinstance(rows[0]["lines"], list)
    order_ten = next(r for r in rows if r["id"] == 10)
    assert len(order_ten["lines"]) == 2
    order_twenty = next(r for r in rows if r["id"] == 20)
    assert len(order_twenty["lines"]) == 1


def test_list_expand_forward_returns_object(
    expand_pg: dict[str, Any], conn: sqlite3.Connection
) -> None:
    rows = execute(
        conn, expand_pg["ep_list_lines"], expand_pg["lines_ds"], {"expand": "order"}
    )
    assert len(rows) == 3
    for row in rows:
        assert "order" in row
        assert isinstance(row["order"], dict)
        assert "name" in row["order"]


def test_expand_does_not_affect_row_count(
    expand_pg: dict[str, Any], conn: sqlite3.Connection
) -> None:
    without = execute(conn, expand_pg["ep_list_orders"], expand_pg["orders_ds"], {})
    with_exp = execute(
        conn, expand_pg["ep_list_orders"], expand_pg["orders_ds"], {"expand": "lines"}
    )
    assert len(with_exp) == len(without)


def test_get_expand_returns_expanded_key(
    expand_pg: dict[str, Any], conn: sqlite3.Connection
) -> None:
    row = execute(
        conn,
        expand_pg["ep_get_order"],
        expand_pg["orders_ds"],
        {"id": 10, "expand": "lines"},
    )
    assert "lines" in row
    assert isinstance(row["lines"], list)
    assert len(row["lines"]) == 2


def test_summary_fields_plus_expand_keeps_expanded_key(
    expand_pg: dict[str, Any], conn: sqlite3.Connection
) -> None:
    rows = execute(
        conn,
        expand_pg["ep_list_orders_summary"],
        expand_pg["orders_ds"],
        {"expand": "lines"},
    )
    for row in rows:
        assert "id" in row
        assert "name" in row
        assert "lines" in row


def test_unknown_expand_name_errors(
    expand_pg: dict[str, Any], conn: sqlite3.Connection
) -> None:
    with pytest.raises(service.ServiceError):
        execute(
            conn,
            expand_pg["ep_list_orders"],
            expand_pg["orders_ds"],
            {"expand": "nope"},
        )


def test_dotted_expand_path_errors(
    expand_pg: dict[str, Any], conn: sqlite3.Connection
) -> None:
    with pytest.raises(service.ServiceError):
        execute(
            conn,
            expand_pg["ep_list_orders"],
            expand_pg["orders_ds"],
            {"expand": "lines.items"},
        )


def test_more_than_5_expand_names_errors(
    expand_pg: dict[str, Any], conn: sqlite3.Connection
) -> None:
    with pytest.raises(service.ServiceError):
        execute(
            conn,
            expand_pg["ep_list_orders"],
            expand_pg["orders_ds"],
            {"expand": "a,b,c,d,e,f"},
        )


def test_expand_on_create_rejected(
    expand_pg: dict[str, Any], conn: sqlite3.Connection
) -> None:
    with pytest.raises(service.ServiceError) as exc:
        execute(
            conn,
            expand_pg["ep_create_order"],
            expand_pg["orders_ds"],
            {"name": "New", "expand": "lines"},
        )
    assert exc.value.code == "invalid_params"


def test_expand_on_update_rejected(
    expand_pg: dict[str, Any], conn: sqlite3.Connection
) -> None:
    with pytest.raises(service.ServiceError) as exc:
        execute(
            conn,
            expand_pg["ep_update_order"],
            expand_pg["orders_ds"],
            {"id": 10, "name": "Changed", "expand": "lines"},
        )
    assert exc.value.code == "invalid_params"


def test_expand_on_delete_rejected(
    expand_pg: dict[str, Any], conn: sqlite3.Connection
) -> None:
    with pytest.raises(service.ServiceError) as exc:
        execute(
            conn,
            expand_pg["ep_delete_order"],
            expand_pg["orders_ds"],
            {"id": 10, "expand": "lines"},
        )
    assert exc.value.code == "invalid_params"
