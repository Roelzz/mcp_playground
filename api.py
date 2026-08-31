"""Thin HTTP wrapper over service.py. No business logic lives here."""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import service
from auth import Principal, get_conn, require_admin, require_write
from models import (
    DatasetCreate,
    DatasetUpdate,
    EndpointCreate,
    EndpointUpdate,
    LLMEndpointCreate,
    LLMEndpointUpdate,
    LLMResponseSpec,
    RecipeCreate,
    RecipeToolsPayload,
    RecipeUpdate,
    RelationshipCreate,
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
Write = Depends(require_write)

READ_TOOL_TYPES = frozenset({"list", "get", "search"})


class CloneServerRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str | None = None


class BulkCloneServerRequest(BaseModel):
    prefix: str
    count: int
    start: int = 1


class ResetAllToSeedRequest(BaseModel):
    prefix: str | None = None


def _assert_tool_allowed(
    conn: sqlite3.Connection, slug: str, tool_name: str, principal: Principal
) -> None:
    """Readonly principals may run read-shaped tools but not create/update/delete ones."""
    if principal.can_write:
        return
    server = _handle(service.get_server_by_slug, conn, slug)
    for endpoint in _handle(service.list_endpoints, conn, server["id"]):
        if endpoint["tool_name"] == tool_name:
            if endpoint.get("tool_type") not in READ_TOOL_TYPES:
                raise HTTPException(status_code=403, detail="readonly key cannot write")
            return


# ------------------------------------------------------------------------- servers


@router.get("/servers")
def list_servers(conn: sqlite3.Connection = Conn, _: Principal = Admin) -> list[dict[str, Any]]:
    return _handle(service.list_servers, conn)


@router.post("/servers", status_code=201)
def create_server(
    body: ServerCreate, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> dict[str, Any]:
    return _handle(
        service.create_server, conn, body.slug, body.name, body.description, body.auth_mode
    )


@router.post("/servers/{server_id}/clone", status_code=201)
def clone_server(
    server_id: int,
    body: CloneServerRequest,
    conn: sqlite3.Connection = Conn,
    _: Principal = Write,
) -> dict[str, Any]:
    return _handle(service.clone_server, conn, server_id, body.slug, body.name)


@router.post("/servers/{server_id}/bulk-clone", status_code=201)
def bulk_clone_server(
    server_id: int,
    body: BulkCloneServerRequest,
    conn: sqlite3.Connection = Conn,
    _: Principal = Write,
) -> dict[str, list[dict[str, Any]]]:
    return _handle(service.bulk_clone_server, conn, server_id, body.prefix, body.count, body.start)


@router.get("/catalog")
def get_catalog(conn: sqlite3.Connection = Conn, _: Principal = Admin) -> list[dict[str, Any]]:
    return _handle(service.get_catalog, conn)


@router.get("/cohort")
def get_cohort(conn: sqlite3.Connection = Conn, _: Principal = Admin) -> list[dict[str, Any]]:
    return _handle(service.get_cohort, conn)


@router.post("/servers/reset-all-to-seed")
def reset_all_to_seed(
    body: ResetAllToSeedRequest | None = None,
    conn: sqlite3.Connection = Conn,
    _: Principal = Write,
) -> dict[str, Any]:
    return _handle(service.reset_all_to_seed, conn, body.prefix if body is not None else None)


@router.get("/servers/{server_id}")
def get_server(
    server_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.get_server, conn, server_id)


@router.patch("/servers/{server_id}")
def update_server(
    server_id: int, body: ServerUpdate, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> dict[str, Any]:
    return _handle(service.update_server, conn, server_id, **body.model_dump(exclude_none=True))


@router.delete("/servers/{server_id}", status_code=204)
def delete_server(server_id: int, conn: sqlite3.Connection = Conn, _: Principal = Write) -> None:
    _handle(service.delete_server, conn, server_id)


# ------------------------------------------------------------------------ datasets


@router.get("/servers/{server_id}/datasets")
def list_datasets(
    server_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.list_datasets, conn, server_id)


@router.post("/servers/{server_id}/datasets", status_code=201)
def create_dataset(
    server_id: int, body: DatasetCreate, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> dict[str, Any]:
    return _handle(service.create_dataset, conn, server_id, body.key, body.id_field, body.rows)


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.get_dataset, conn, dataset_id)


@router.patch("/datasets/{dataset_id}")
def update_dataset(
    dataset_id: int, body: DatasetUpdate, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> dict[str, Any]:
    return _handle(service.update_dataset, conn, dataset_id, **body.model_dump(exclude_none=True))


@router.delete("/datasets/{dataset_id}", status_code=204)
def delete_dataset(dataset_id: int, conn: sqlite3.Connection = Conn, _: Principal = Write) -> None:
    _handle(service.delete_dataset, conn, dataset_id)


# ---------------------------------------------------------------------------- rows


@router.get("/datasets/{dataset_id}/rows")
def list_rows(
    dataset_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.list_rows, conn, dataset_id)


@router.put("/datasets/{dataset_id}/rows")
def replace_rows(
    dataset_id: int, body: RowsPayload, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> dict[str, Any]:
    return _handle(service.replace_rows, conn, dataset_id, body.rows)


@router.post("/datasets/{dataset_id}/rows")
def add_rows(
    dataset_id: int, body: RowsPayload, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> dict[str, Any]:
    return _handle(service.add_rows, conn, dataset_id, body.rows)


@router.post("/datasets/{dataset_id}/reset-to-seed")
def reset_to_seed(
    dataset_id: int, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> dict[str, Any]:
    return _handle(service.reset_to_seed, conn, dataset_id)


@router.post("/datasets/{dataset_id}/save-as-seed")
def save_as_seed(
    dataset_id: int, conn: sqlite3.Connection = Conn, _: Principal = Write
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
    server_id: int, body: EndpointCreate, conn: sqlite3.Connection = Conn, _: Principal = Write
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
    endpoint_id: int, body: EndpointUpdate, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> dict[str, Any]:
    return _handle(service.update_endpoint, conn, endpoint_id, **body.model_dump(exclude_none=True))


@router.delete("/endpoints/{endpoint_id}", status_code=204)
def delete_endpoint(
    endpoint_id: int, conn: sqlite3.Connection = Conn, _: Principal = Write
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
    body: LLMEndpointCreate, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> dict[str, Any]:
    return _handle(service.create_llm_endpoint, conn, **body.model_dump())


@router.get("/llm-endpoints/{llm_id}")
def get_llm_endpoint(
    llm_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.get_llm_endpoint, conn, llm_id)


@router.patch("/llm-endpoints/{llm_id}")
def update_llm_endpoint(
    llm_id: int, body: LLMEndpointUpdate, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> dict[str, Any]:
    return _handle(service.update_llm_endpoint, conn, llm_id, **body.model_dump(exclude_none=True))


@router.delete("/llm-endpoints/{llm_id}", status_code=204)
def delete_llm_endpoint(llm_id: int, conn: sqlite3.Connection = Conn, _: Principal = Write) -> None:
    _handle(service.delete_llm_endpoint, conn, llm_id)


@router.put("/llm-endpoints/{llm_id}/responses")
def set_llm_responses(
    llm_id: int,
    body: list[LLMResponseSpec],
    conn: sqlite3.Connection = Conn,
    _: Principal = Write,
) -> dict[str, Any]:
    return _handle(service.set_llm_responses, conn, llm_id, [r.model_dump() for r in body])


# --------------------------------------------------------------------------- recipes


@router.get("/recipes/validate")
def validate_recipes(conn: sqlite3.Connection = Conn, _: Principal = Admin) -> dict[str, Any]:
    return _handle(service.validate_recipes, conn)


@router.get("/recipes/departments")
def recipe_departments(
    conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.recipe_departments, conn)


@router.get("/recipes")
def list_recipes(
    published_only: bool = False,
    conn: sqlite3.Connection = Conn,
    _: Principal = Admin,
) -> list[dict[str, Any]]:
    return _handle(service.list_recipes, conn, published_only)


@router.post("/recipes", status_code=201)
def create_recipe(
    body: RecipeCreate, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> dict[str, Any]:
    return _handle(service.create_recipe, conn, **body.model_dump())


@router.get("/recipes/{recipe_id}")
def get_recipe(
    recipe_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.get_recipe, conn, recipe_id)


@router.patch("/recipes/{recipe_id}")
def update_recipe(
    recipe_id: int,
    body: RecipeUpdate,
    conn: sqlite3.Connection = Conn,
    _: Principal = Write,
) -> dict[str, Any]:
    return _handle(service.update_recipe, conn, recipe_id, **body.model_dump(exclude_none=True))


@router.delete("/recipes/{recipe_id}", status_code=204)
def delete_recipe(recipe_id: int, conn: sqlite3.Connection = Conn, _: Principal = Write) -> None:
    _handle(service.delete_recipe, conn, recipe_id)


@router.put("/recipes/{recipe_id}/tools")
def set_recipe_tools(
    recipe_id: int,
    body: RecipeToolsPayload,
    conn: sqlite3.Connection = Conn,
    _: Principal = Write,
) -> dict[str, Any]:
    return _handle(
        service.set_recipe_tools,
        conn,
        recipe_id,
        [tool.model_dump() for tool in body.tools],
    )


# ------------------------------------------------------------------------ tool call


@router.post("/servers/{slug}/tools/{tool_name}/call")
def call_tool(
    slug: str,
    tool_name: str,
    body: dict[str, Any] | None = None,
    conn: sqlite3.Connection = Conn,
    principal: Principal = Admin,
) -> Any:
    _assert_tool_allowed(conn, slug, tool_name, principal)
    return {
        "result": _handle(
            service.call_tool, conn, slug, tool_name, body or {}, "api", principal.label
        )
    }


# --------------------------------------------------------------------- observability


@router.get("/traffic")
def get_traffic(
    target_slug: str | None = None, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.get_traffic, conn, target_slug)


@router.get("/traffic/summary")
def get_traffic_summary(
    conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.get_traffic_summary, conn)


@router.delete("/traffic", status_code=204)
def clear_traffic(conn: sqlite3.Connection = Conn, _: Principal = Write) -> None:
    _handle(service.clear_traffic, conn)


# --------------------------------------------------------------------- relationships


class EnsureDemoRequest(BaseModel):
    server_id: int | None = None


@router.get("/servers/{server_id}/relationships/validate")
def validate_relationships(
    server_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> dict[str, Any]:
    return _handle(service.validate_relationships, conn, server_id)


@router.get("/servers/{server_id}/relationships")
def list_relationships(
    server_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.list_relationships, conn, server_id)


@router.post("/servers/{server_id}/relationships", status_code=201)
def create_relationship(
    server_id: int,
    body: RelationshipCreate,
    conn: sqlite3.Connection = Conn,
    _: Principal = Write,
) -> dict[str, Any]:
    return _handle(service.create_relationship, conn, server_id, **body.model_dump())


@router.delete("/relationships/{relationship_id}", status_code=204)
def delete_relationship(
    relationship_id: int, conn: sqlite3.Connection = Conn, _: Principal = Write
) -> None:
    _handle(service.delete_relationship, conn, relationship_id)


@router.post("/relationships/ensure-demo")
def ensure_demo_relationships(
    body: EnsureDemoRequest | None = None,
    conn: sqlite3.Connection = Conn,
    _: Principal = Write,
) -> dict[str, Any]:
    server_id = body.server_id if body is not None else None
    return _handle(service.ensure_demo_relationships, conn, server_id)


@router.get("/datasets/{dataset_id}/expands")
def get_available_expands(
    dataset_id: int, conn: sqlite3.Connection = Conn, _: Principal = Admin
) -> list[dict[str, Any]]:
    return _handle(service.available_expands, conn, dataset_id)
