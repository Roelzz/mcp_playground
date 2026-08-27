from typing import Any

import pytest

import db
import service
import store


@pytest.fixture
def conn(tmp_path):
    return db.init_db(str(tmp_path / "test.db"))


def _create_server(conn, slug: str = "acme") -> dict[str, Any]:
    return service.create_server(conn, slug, slug.title(), f"{slug} description", "none")


def _create_dataset(
    conn,
    server_id: int,
    rows: list[dict[str, Any]] | None = None,
    key: str = "orders",
) -> dict[str, Any]:
    return service.create_dataset(conn, server_id, key, "id", rows or [])


def _create_endpoint(
    conn,
    server_id: int,
    dataset_id: int,
    path: str = "/orders",
    method: str = "GET",
    tool_name: str = "list_orders",
    summary_fields: list[str] | None = None,
) -> dict[str, Any]:
    return service.create_endpoint(
        conn,
        server_id,
        path,
        method,
        tool_name,
        dataset_id=dataset_id,
        summary_fields=summary_fields,
    )


def _clean_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in row.items() if key != "_row_id"} for row in rows]


@pytest.mark.parametrize("slug", ["Bad Slug", "UPPER", "-leading"])
def test_invalid_slugs_raise_service_error(conn, slug: str) -> None:
    with pytest.raises(service.ServiceError) as exc:
        service.create_server(conn, slug, "Bad")

    assert exc.value.__class__ is service.ServiceError
    assert exc.value.code == "invalid"


def test_underscore_slug_is_reserved(conn) -> None:
    with pytest.raises(service.ServiceError) as exc:
        service.create_server(conn, "_admin", "Admin")

    assert exc.value.__class__ is service.ServiceError
    assert exc.value.code == "invalid"
    assert "reserved" in exc.value.message


def test_duplicate_server_slug_raises_conflict(conn) -> None:
    service.create_server(conn, "acme", "Acme")

    with pytest.raises(service.Conflict) as exc:
        service.create_server(conn, "acme", "Other")

    assert exc.value.code == "conflict"


def test_duplicate_llm_slug_raises_conflict(conn) -> None:
    service.create_llm_endpoint(conn, slug="mock", name="Mock", mode="mock")

    with pytest.raises(service.Conflict) as exc:
        service.create_llm_endpoint(conn, slug="mock", name="Other", mode="mock")

    assert exc.value.code == "conflict"


@pytest.mark.parametrize("tool_name", ["has-dash", "9leading"])
def test_invalid_tool_names_raise_service_error(conn, tool_name: str) -> None:
    server = _create_server(conn)
    dataset = _create_dataset(conn, server["id"])

    with pytest.raises(service.ServiceError) as exc:
        _create_endpoint(conn, server["id"], dataset["id"], tool_name=tool_name)

    assert exc.value.__class__ is service.ServiceError
    assert exc.value.code == "invalid"


def test_duplicate_tool_name_conflicts_only_within_same_server(conn) -> None:
    server_a = _create_server(conn, "acme")
    dataset_a = _create_dataset(conn, server_a["id"])
    _create_endpoint(conn, server_a["id"], dataset_a["id"], tool_name="list_orders")

    with pytest.raises(service.Conflict) as exc:
        _create_endpoint(
            conn,
            server_a["id"],
            dataset_a["id"],
            "/orders/search",
            "GET",
            "list_orders",
        )

    assert exc.value.code == "conflict"

    server_b = _create_server(conn, "beta")
    dataset_b = _create_dataset(conn, server_b["id"])
    endpoint = _create_endpoint(conn, server_b["id"], dataset_b["id"], tool_name="list_orders")
    assert endpoint["tool_name"] == "list_orders"


def test_endpoint_dataset_must_belong_to_same_server(conn) -> None:
    server_a = _create_server(conn, "acme")
    dataset_a = _create_dataset(conn, server_a["id"])
    server_b = _create_server(conn, "beta")

    with pytest.raises(service.ServiceError) as exc:
        _create_endpoint(conn, server_b["id"], dataset_a["id"])

    assert exc.value.__class__ is service.ServiceError
    assert exc.value.code == "invalid"
    assert "belongs to server" in exc.value.message


@pytest.mark.parametrize(("method", "path"), [("POST", "/orders/{id}"), ("DELETE", "/orders")])
def test_unclassifiable_endpoint_shapes_raise_service_error(
    conn, method: str, path: str
) -> None:
    server = _create_server(conn)
    dataset = _create_dataset(conn, server["id"])

    with pytest.raises(service.ServiceError) as exc:
        _create_endpoint(conn, server["id"], dataset["id"], path, method, "bad_shape")

    assert exc.value.__class__ is service.ServiceError
    assert exc.value.code == "invalid"
    assert "Unsupported endpoint shape" in exc.value.message


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/orders", "list"),
        ("GET", "/orders/search", "search"),
        ("GET", "/orders/{id}", "get"),
        ("POST", "/orders", "create"),
        ("PATCH", "/orders/{id}", "update"),
        ("DELETE", "/orders/{id}", "delete"),
    ],
)
def test_create_endpoint_infers_tool_type(
    conn, method: str, path: str, expected: str
) -> None:
    server = _create_server(conn)
    dataset = _create_dataset(conn, server["id"])
    endpoint = _create_endpoint(
        conn,
        server["id"],
        dataset["id"],
        path,
        method,
        f"{expected}_orders",
    )

    assert endpoint["tool_type"] == expected


@pytest.mark.parametrize(("method", "path"), [("GET", "/orders/{id}"), ("POST", "/orders")])
def test_summary_fields_only_allowed_on_list_and_search(
    conn, method: str, path: str
) -> None:
    server = _create_server(conn)
    dataset = _create_dataset(conn, server["id"])

    with pytest.raises(service.ServiceError) as exc:
        _create_endpoint(
            conn,
            server["id"],
            dataset["id"],
            path,
            method,
            "bad_summary",
            summary_fields=["id"],
        )

    assert exc.value.__class__ is service.ServiceError
    assert exc.value.code == "invalid"
    assert "summary_fields" in exc.value.message


def test_delete_dataset_refuses_referenced_dataset_then_allows_after_endpoint_delete(conn) -> None:
    server = _create_server(conn)
    dataset = _create_dataset(conn, server["id"])
    endpoint = _create_endpoint(conn, server["id"], dataset["id"])

    with pytest.raises(service.Conflict) as exc:
        service.delete_dataset(conn, dataset["id"])

    assert exc.value.code == "conflict"

    service.delete_endpoint(conn, endpoint["id"])
    service.delete_dataset(conn, dataset["id"])

    with pytest.raises(service.NotFound):
        service.get_dataset(conn, dataset["id"])


def test_create_dataset_seeds_live_and_seed_tiers(conn) -> None:
    server = _create_server(conn)
    rows = [{"id": 1, "name": "Alpha"}, {"id": 2, "name": "Beta"}]

    dataset = _create_dataset(conn, server["id"], rows)
    fetched = service.get_dataset(conn, dataset["id"])

    assert fetched["row_count"] == len(rows)
    assert fetched["seed_count"] == len(rows)

    service.replace_rows(conn, dataset["id"], [{"id": 99, "name": "Mutated"}])
    restored = service.reset_to_seed(conn, dataset["id"])
    assert restored["row_count"] == len(rows)
    assert _clean_rows(service.list_rows(conn, dataset["id"])) == rows


def test_seed_round_trip_reset_and_save_as_seed(conn) -> None:
    server = _create_server(conn)
    original_rows = [{"id": 1, "status": "open", "name": "Alpha"}]
    dataset = _create_dataset(conn, server["id"], original_rows)
    _create_endpoint(conn, server["id"], dataset["id"], "/orders/{id}", "PATCH", "update_order")

    updated = service.call_tool(conn, "acme", "update_order", {"id": 1, "status": "closed"})
    assert updated["status"] == "closed"
    assert service.list_rows(conn, dataset["id"])[0]["status"] == "closed"

    service.reset_to_seed(conn, dataset["id"])
    assert _clean_rows(service.list_rows(conn, dataset["id"])) == original_rows

    service.call_tool(conn, "acme", "update_order", {"id": 1, "status": "archived"})
    service.save_as_seed(conn, dataset["id"])
    service.call_tool(conn, "acme", "update_order", {"id": 1, "status": "temporary"})
    service.reset_to_seed(conn, dataset["id"])
    assert _clean_rows(service.list_rows(conn, dataset["id"])) == [
        {"id": 1, "status": "archived", "name": "Alpha"}
    ]


@pytest.mark.parametrize(
    ("fn", "args"),
    [
        (service.get_server, (9999,)),
        (service.get_dataset, (9999,)),
        (service.get_endpoint, (9999,)),
        (service.get_llm_endpoint, (9999,)),
        (service.get_server_by_slug, ("nope",)),
    ],
)
def test_not_found_paths_raise_not_found(conn, fn, args: tuple[Any, ...]) -> None:
    with pytest.raises(service.NotFound) as exc:
        fn(conn, *args)

    assert exc.value.code == "not_found"


def test_call_tool_unknowns_and_success(conn) -> None:
    server = _create_server(conn)
    rows = [{"id": 1, "name": "Alpha"}]
    dataset = _create_dataset(conn, server["id"], rows)
    _create_endpoint(conn, server["id"], dataset["id"])

    with pytest.raises(service.NotFound):
        service.call_tool(conn, "missing", "list_orders", {})

    with pytest.raises(service.NotFound):
        service.call_tool(conn, "acme", "missing_tool", {})

    assert service.call_tool(conn, "acme", "list_orders", {}) == rows


def test_proxy_llm_requires_upstream_url_but_mock_does_not(conn) -> None:
    with pytest.raises(service.ServiceError) as exc:
        service.create_llm_endpoint(conn, slug="proxy", name="Proxy", mode="proxy")

    assert exc.value.__class__ is service.ServiceError
    assert "upstream_url" in exc.value.message

    llm = service.create_llm_endpoint(conn, slug="mock", name="Mock", mode="mock")
    assert llm["slug"] == "mock"


def test_set_llm_responses_defaults_match_type_to_always(conn) -> None:
    llm = service.create_llm_endpoint(conn, slug="mock", name="Mock", mode="mock")

    updated = service.set_llm_responses(
        conn,
        llm["id"],
        [{"match_value": "", "response": "fallback"}],
    )

    assert updated["responses"][0]["match_type"] == "always"
    assert updated["responses"][0]["response"] == "fallback"


@pytest.mark.parametrize(
    "response",
    [
        {"match_type": "sometimes", "response": "bad"},
        {"match_type": "contains", "response": "bad"},
        {"match_type": "regex", "response": "bad"},
        {"match_type": "always", "response": ""},
    ],
)
def test_set_llm_responses_rejects_invalid_specs(conn, response: dict[str, str]) -> None:
    llm = service.create_llm_endpoint(conn, slug="mock", name="Mock", mode="mock")

    with pytest.raises(service.ServiceError) as exc:
        service.set_llm_responses(conn, llm["id"], [response])

    assert exc.value.__class__ is service.ServiceError
    assert exc.value.code == "invalid"


def test_create_endpoint_requires_dataset_id(conn) -> None:
    server = _create_server(conn)

    with pytest.raises(service.ServiceError) as exc:
        service.create_endpoint(conn, server["id"], "/orders", "GET", "list_orders")

    assert exc.value.__class__ is service.ServiceError
    assert exc.value.code == "invalid"
    assert "dataset_id" in exc.value.message


def test_server_and_dataset_enrichment(conn) -> None:
    server = _create_server(conn)
    rows = [
        {"id": 1, "name": "Alpha", "active": True},
        {"id": 2, "name": "Beta", "total": 12.5},
    ]
    dataset = _create_dataset(conn, server["id"], rows)
    _create_endpoint(conn, server["id"], dataset["id"])

    servers = service.list_servers(conn)
    assert servers[0]["dataset_count"] == 1
    assert servers[0]["endpoint_count"] == 1
    assert servers[0]["row_count"] == 2

    fetched = service.get_dataset(conn, dataset["id"])
    assert fetched["row_count"] == 2
    assert fetched["seed_count"] == 2
    assert fetched["field_schema"] == {
        "id": "integer",
        "name": "string",
        "active": "boolean",
        "total": "number",
    }


def test_nested_transaction_rolls_back_completely(conn) -> None:
    from db import transaction

    before = len(store.list_servers(conn))
    with pytest.raises(RuntimeError):
        with transaction(conn):
            store.create_server(conn, "roll", "Roll", "", "none")
            raise RuntimeError("boom")
    assert len(store.list_servers(conn)) == before
    assert db._TX_DEPTH == {}


def test_nested_transaction_success_commits_and_clears_depth(conn) -> None:
    assert db._TX_DEPTH == {}

    with db.transaction(conn):
        store.create_server(conn, "outer", "Outer", "", "none")
        with db.transaction(conn):
            store.create_server(conn, "inner", "Inner", "", "none")

    assert {server["slug"] for server in store.list_servers(conn)} == {"inner", "outer"}
    assert db._TX_DEPTH == {}
