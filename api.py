"""Thin HTTP wrapper over service.py. No business logic lives here."""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

import service
from auth import Principal, get_conn, require_admin
from models import (
    DatasetCreate,
    DatasetUpdate,
    EndpointCreate,
    EndpointUpdate,
    LLMEndpointCreate,
    LLMEndpointUpdate,
    LLMResponseSpec,
    RowsPayload,
    ServerCreate,
    ServerUpdate,
)

router = APIRouter(prefix="/api", tags=["admin"])

STATUS = {"not_found": 404, "conflict": 409, "invalid": 400}


def _handle(fn, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except service.ServiceError as exc:
        raise HTTPException(status_code=STATUS.get(exc.code, 400), detail=exc.message) from exc


Conn = Depends(get_conn)
Admin = Depends(require_admin)


# ------------------------------------------------------------------------- servers


@router.get("/servers")
def list_servers(conn: sqlite3.Connection = Conn, _: Principal = Admin) -> list[dict[str, Any]]:
    return _handle(service.list_servers, conn)


@router.post("/servers", status_code=201)
def create_server(
    body: ServerCreate, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(
        service.create_server, conn, body.slug, body.name, body.description, body.auth_mode
    )


@router.get("/servers/{server_id}")
def get_server(
    server_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.get_server, conn, server_id)


@router.patch("/servers/{server_id}")
def update_server(
    server_id: int, body: ServerUpdate, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.update_server, conn, server_id, **body.model_dump(exclude_none=True))


@router.delete("/servers/{server_id}", status_code=204)
def delete_server(server_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin) -> None:
    _handle(service.delete_server, conn, server_id)


# ------------------------------------------------------------------------ datasets


@router.get("/servers/{server_id}/datasets")
def list_datasets(
    server_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.list_datasets, conn, server_id)


@router.post("/servers/{server_id}/datasets", status_code=201)
def create_dataset(
    server_id: int, body: DatasetCreate, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.create_dataset, conn, server_id, body.key, body.id_field, body.rows)


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.get_dataset, conn, dataset_id)


@router.patch("/datasets/{dataset_id}")
def update_dataset(
    dataset_id: int, body: DatasetUpdate, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.update_dataset, conn, dataset_id, **body.model_dump(exclude_none=True))


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin) -> None:
    _handle(service.delete_dataset, conn, dataset_id)


# ---------------------------------------------------------------------------- rows


@router.get("/datasets/{dataset_id}/rows")
def list_rows(
    dataset_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.list_rows, conn, dataset_id)


@router.put("/datasets/{dataset_id}/rows")
def replace_rows(
    dataset_id: int, body: RowsPayload, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.replace_rows, conn, dataset_id, body.rows)


@router.post("/datasets/{dataset_id}/rows")
def add_rows(
    dataset_id: int, body: RowsPayload, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.add_rows, conn, dataset_id, body.rows)


@router.post("/datasets/{dataset_id}/reset-to-seed")
def reset_to_seed(
    dataset_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.reset_to_seed, conn, dataset_id)


@router.post("/datasets/{dataset_id}/save-as-seed")
def save_as_seed(
    dataset_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.save_as_seed, conn, dataset_id)


# ----------------------------------------------------------------------- endpoints


@router.get("/servers/{server_id}/endpoints")
def list_endpoints(
    server_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.list_endpoints, conn, server_id)


@router.post("/servers/{server_id}/endpoints", status_code=201)
def create_endpoint(
    server_id: int, body: EndpointCreate, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(
        service.create_endpoint,
        conn,
        server_id,
        body.path,
        body.method,
        body.tool_name,
        body.description,
        body.dataset_id,
        body.summary_fields,
    )


@router.get("/endpoints/{endpoint_id}")
def get_endpoint(
    endpoint_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.get_endpoint, conn, endpoint_id)


@router.patch("/endpoints/{endpoint_id}")
def update_endpoint(
    endpoint_id: int, body: EndpointUpdate, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(
        service.update_endpoint, conn, endpoint_id, **body.model_dump(exclude_none=True)
    )


@router.delete("/endpoints/{endpoint_id}", status_code=204)
def delete_endpoint(
    endpoint_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> None:
    _handle(service.delete_endpoint, conn, endpoint_id)


# -------------------------------------------------------------------- llm endpoints


@router.get("/llm-endpoints")
def list_llm_endpoints(
    conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.list_llm_endpoints, conn)


@router.post("/llm-endpoints", status_code=201)
def create_llm_endpoint(
    body: LLMEndpointCreate, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.create_llm_endpoint, conn, **body.model_dump())


@router.get("/llm-endpoints/{llm_id}")
def get_llm_endpoint(
    llm_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.get_llm_endpoint, conn, llm_id)


@router.patch("/llm-endpoints/{llm_id}")
def update_llm_endpoint(
    llm_id: int, body: LLMEndpointUpdate, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(
        service.update_llm_endpoint, conn, llm_id, **body.model_dump(exclude_none=True)
    )


@router.delete("/llm-endpoints/{llm_id}", status_code=204)
def delete_llm_endpoint(llm_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin) -> None:
    _handle(service.delete_llm_endpoint, conn, llm_id)


@router.put("/llm-endpoints/{llm_id}/responses")
def set_llm_responses(
    llm_id: int,
    body: list[LLMResponseSpec],
    conn: sqlite3.Connection = Conn,
    _: Principal = Admin,
) -> dict[str, Any]:
    return _handle(service.set_llm_responses, conn, llm_id, [r.model_dump() for r in body])


# ------------------------------------------------------------------------ tool call


@router.post("/servers/{slug}/tools/{tool_name}/call")
def call_tool(
    slug: str,
    tool_name: str,
    body: dict[str, Any] | None = None,
    conn: sqlite3.Connection = Conn,
    _: Principal = Admin,
) -> Any:
    return {"result": _handle(service.call_tool, conn, slug, tool_name, body or {})}


# --------------------------------------------------------------------- observability


@router.get("/traffic")
def get_traffic(
    target_slug: str | None = None, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.get_traffic, conn, target_slug)


@router.delete("/traffic", status_code=204)
def clear_traffic(conn: sqlite3.Connection = Conn, _: Principal = Admin) -> None:
    _handle(service.clear_traffic, conn)
