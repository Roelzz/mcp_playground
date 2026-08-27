"""Pydantic request/response models for the admin API."""

from typing import Any, Literal

from pydantic import BaseModel, Field

AuthMode = Literal["none", "api_key"]
LLMMode = Literal["mock", "proxy"]
MatchType = Literal["always", "contains", "regex"]
HTTPMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]


class ServerCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1)
    description: str = ""
    auth_mode: AuthMode = "none"


class ServerUpdate(BaseModel):
    slug: str | None = None
    name: str | None = None
    description: str | None = None
    auth_mode: AuthMode | None = None


class DatasetCreate(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    id_field: str = "id"
    rows: list[dict[str, Any]] = Field(default_factory=list)


class DatasetUpdate(BaseModel):
    key: str | None = None
    id_field: str | None = None


class RowsPayload(BaseModel):
    rows: list[dict[str, Any]]


class EndpointCreate(BaseModel):
    path: str = Field(min_length=1)
    method: HTTPMethod = "GET"
    tool_name: str = Field(min_length=1, max_length=64)
    description: str = ""
    dataset_id: int
    summary_fields: list[str] = Field(default_factory=list)


class EndpointUpdate(BaseModel):
    path: str | None = None
    method: HTTPMethod | None = None
    tool_name: str | None = None
    description: str | None = None
    dataset_id: int | None = None
    summary_fields: list[str] | None = None


class LLMResponseSpec(BaseModel):
    match_type: MatchType = "always"
    match_value: str = ""
    response: str


class LLMEndpointCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1)
    description: str = ""
    mode: LLMMode = "mock"
    model_name: str = "playground-model"
    upstream_url: str | None = None
    upstream_key: str | None = None
    upstream_deployment: str | None = None
    system_prompt: str | None = None
    auth_mode: AuthMode = "none"


class LLMEndpointUpdate(BaseModel):
    slug: str | None = None
    name: str | None = None
    description: str | None = None
    mode: LLMMode | None = None
    model_name: str | None = None
    upstream_url: str | None = None
    upstream_key: str | None = None
    upstream_deployment: str | None = None
    system_prompt: str | None = None
    auth_mode: AuthMode | None = None


RelationType = Literal["many_to_one", "one_to_one"]


class RelationshipCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    source_dataset_id: int
    source_field: str = Field(min_length=1)
    target_dataset_id: int
    target_field: str = Field(min_length=1)
    relation_type: RelationType = "many_to_one"
    expand_name: str = Field(min_length=1)
    inverse_expand_name: str | None = None
    required: bool = False
    description: str | None = None
