import sqlite3
from typing import Any

import service
import store


class ExecutorError(Exception):
    def __init__(self, message: str, code: str = "error") -> None:
        super().__init__(message)
        self.code = code


def infer_tool_type(method: str, path: str) -> str:
    method_name = method.upper()
    segments = _path_segments(path)

    if len(segments) == 1:
        if method_name == "GET":
            return "list"
        if method_name == "POST":
            return "create"
    elif len(segments) == 2:
        last_segment = segments[1]
        if method_name == "GET" and last_segment == "search":
            return "search"
        if _is_path_param(last_segment):
            if method_name == "GET":
                return "get"
            if method_name in {"PUT", "PATCH"}:
                return "update"
            if method_name == "DELETE":
                return "delete"

    raise ExecutorError(f"Unsupported endpoint shape: {method} {path}", code="invalid_endpoint")


def path_param_name(path: str) -> str | None:
    normalized = path.rstrip("/")
    last_segment = normalized.rsplit("/", 1)[-1]
    if _is_path_param(last_segment):
        return last_segment[1:-1]
    return None


def derive_field_schema(rows: list[dict[str, Any]]) -> dict[str, str]:
    schema: dict[str, str] = {}
    typed_fields: set[str] = set()

    for row in rows:
        for key, value in row.items():
            if key == "_row_id":
                continue
            if key not in schema:
                schema[key] = "string"
            if key not in typed_fields and value is not None:
                schema[key] = _type_name(value)
                typed_fields.add(key)

    return schema


def execute(
    conn: sqlite3.Connection,
    endpoint: dict[str, Any],
    dataset: dict[str, Any],
    params: dict[str, Any],
) -> Any:
    _validate_endpoint_dataset(endpoint, dataset)
    tool_type = infer_tool_type(str(endpoint["method"]), str(endpoint["path"]))

    # Strip expand BEFORE any filtering so it never leaks into equality-filter logic.
    expand_value = params.pop("expand", None)

    if tool_type in {"create", "update", "delete"}:
        if expand_value is not None:
            raise service.ServiceError(
                f"expand is not supported on {tool_type} operations",
                code="invalid_params",
            )

    expand_names = service.parse_expand(expand_value)

    if tool_type == "list":
        return _execute_list(
            conn, endpoint, dataset, params, search=False, expand_names=expand_names
        )
    if tool_type == "search":
        return _execute_list(
            conn, endpoint, dataset, params, search=True, expand_names=expand_names
        )
    if tool_type == "get":
        row = _find_row(conn, endpoint, dataset, params)
        rows = service.expand_rows(conn, int(dataset["id"]), [_strip_row_id(row)], expand_names)
        return rows[0]
    if tool_type == "create":
        return _execute_create(conn, dataset, params)
    if tool_type == "update":
        return _execute_update(conn, endpoint, dataset, params)
    if tool_type == "delete":
        return _execute_delete(conn, endpoint, dataset, params)

    raise ExecutorError(f"Unsupported tool type: {tool_type}", code="invalid_endpoint")


def _path_segments(path: str) -> list[str]:
    if not path.startswith("/"):
        raise ExecutorError(f"Unsupported endpoint shape: path must start with /: {path}")
    normalized = path.rstrip("/")
    segments = normalized.split("/")[1:]
    if not segments or any(segment == "" for segment in segments):
        raise ExecutorError(f"Unsupported endpoint shape: invalid path {path}")
    if _is_path_param(segments[0]):
        raise ExecutorError(f"Unsupported endpoint shape: invalid collection path {path}")
    return segments


def _is_path_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}") and len(segment) > 2


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return "string"


def _execute_list(
    conn: sqlite3.Connection,
    endpoint: dict[str, Any],
    dataset: dict[str, Any],
    params: dict[str, Any],
    *,
    search: bool,
    expand_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    rows = store.list_rows(conn, int(dataset["id"]))
    filtered = [_strip_row_id(row) for row in rows if _matches_filters(row, params, search=search)]

    query = params.get("q") if search else None
    if isinstance(query, str) and query:
        q = query.lower()
        filtered = [row for row in filtered if _matches_query(row, q)]

    limited = filtered[: _parse_limit(params)]

    if expand_names:
        limited = service.expand_rows(conn, int(dataset["id"]), limited, expand_names)

    summary_fields = _summary_fields(endpoint)
    if summary_fields:
        expanded_keys = set(expand_names or [])
        return [
            {**_project(row, summary_fields), **{k: row[k] for k in expanded_keys if k in row}}
            for row in limited
        ]
    return limited


def _reject_unknown_fields(rows: list[dict[str, Any]], body: dict[str, Any]) -> None:
    """Reject field names the dataset has never seen. An empty dataset accepts anything."""
    known = derive_field_schema(rows)
    if not known:
        return
    unknown = sorted(key for key in body if key not in known)
    if unknown:
        raise ExecutorError(
            f"unknown field(s): {', '.join(unknown)}. "
            f"known fields: {', '.join(sorted(known))}",
            code="invalid_params",
        )


def _execute_create(
    conn: sqlite3.Connection, dataset: dict[str, Any], params: dict[str, Any]
) -> dict[str, Any]:
    rows = store.list_rows(conn, int(dataset["id"]))
    _reject_unknown_fields(rows, params)
    row = dict(params)
    id_field = _id_field(dataset)
    if id_field not in row:
        row[id_field] = _next_id(rows, id_field)
    store.add_rows(conn, int(dataset["id"]), [row])
    return row


def _execute_update(
    conn: sqlite3.Connection,
    endpoint: dict[str, Any],
    dataset: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    target = _find_row(conn, endpoint, dataset, params)
    param_name = _required_path_param(endpoint)
    body = {key: value for key, value in params.items() if key != param_name}
    _reject_unknown_fields(store.list_rows(conn, int(dataset["id"])), body)
    merged = {**_strip_row_id(target), **body}
    store.update_row(conn, int(target["_row_id"]), merged)
    return merged


def _execute_delete(
    conn: sqlite3.Connection,
    endpoint: dict[str, Any],
    dataset: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    target = _find_row(conn, endpoint, dataset, params)
    id_field = _id_field(dataset)
    deleted_id = target.get(id_field)
    store.delete_row(conn, int(target["_row_id"]))
    return {"deleted": True, id_field: deleted_id}


def _validate_endpoint_dataset(endpoint: dict[str, Any], dataset: dict[str, Any]) -> None:
    for key in ("method", "path", "dataset_id", "summary_fields"):
        if key not in endpoint:
            raise ExecutorError(
                f"Endpoint config missing required key: {key}",
                code="invalid_config",
            )
    for key in ("id", "id_field"):
        if key not in dataset:
            raise ExecutorError(
                f"Dataset config missing required key: {key}",
                code="invalid_config",
            )
    if endpoint["dataset_id"] != dataset["id"]:
        raise ExecutorError("Endpoint dataset_id does not match dataset id", code="invalid_config")
    if not isinstance(endpoint["summary_fields"], list):
        raise ExecutorError("Endpoint summary_fields must be a list", code="invalid_config")
    if not isinstance(dataset["id_field"], str) or dataset["id_field"] == "":
        raise ExecutorError("Dataset id_field must be a non-empty string", code="invalid_config")


def _summary_fields(endpoint: dict[str, Any]) -> list[str]:
    fields = endpoint["summary_fields"]
    if not all(isinstance(field, str) for field in fields):
        raise ExecutorError(
            "Endpoint summary_fields must contain only strings",
            code="invalid_config",
        )
    return fields


def _id_field(dataset: dict[str, Any]) -> str:
    return str(dataset["id_field"])


def _required_path_param(endpoint: dict[str, Any]) -> str:
    param_name = path_param_name(str(endpoint["path"]))
    if param_name is None:
        raise ExecutorError(
            f"Endpoint path has no path parameter: {endpoint['path']}",
            code="invalid_endpoint",
        )
    return param_name


def _find_row(
    conn: sqlite3.Connection,
    endpoint: dict[str, Any],
    dataset: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    param_name = _required_path_param(endpoint)
    if param_name not in params:
        raise ExecutorError(f"Missing path parameter: {param_name}", code="invalid_params")

    id_field = _id_field(dataset)
    lookup_value = params[param_name]
    for row in store.list_rows(conn, int(dataset["id"])):
        if id_field not in row:
            continue
        stored_id = row[id_field]
        if stored_id == _coerce(lookup_value, stored_id):
            return row

    raise ExecutorError(f"Row with {id_field}={lookup_value!r} not found", code="not_found")


def _matches_filters(row: dict[str, Any], params: dict[str, Any], *, search: bool) -> bool:
    ignored = {"limit"}
    if search:
        ignored.add("q")

    for key, value in params.items():
        if key in ignored:
            continue
        stored_value = row.get(key)
        if stored_value != _coerce(value, stored_value):
            return False
    return True


def _matches_query(row: dict[str, Any], q: str) -> bool:
    return any(q in value.lower() for value in row.values() if isinstance(value, str))


def _parse_limit(params: dict[str, Any]) -> int:
    raw_limit = params.get("limit", 50)
    if raw_limit is None:
        return 50
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError) as exc:
        raise ExecutorError(f"Invalid limit: {raw_limit!r}", code="invalid_params") from exc
    if limit < 0:
        raise ExecutorError(f"Invalid limit: {limit}", code="invalid_params")
    return limit


def _project(row: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: row[field] for field in fields if field in row}


def _strip_row_id(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_row_id"}


def _next_id(rows: list[dict[str, Any]], id_field: str) -> int | float:
    numeric_ids = [
        row[id_field]
        for row in rows
        if isinstance(row.get(id_field), int | float) and not isinstance(row[id_field], bool)
    ]
    if not numeric_ids:
        return 1
    return max(numeric_ids) + 1


def _coerce(value: Any, like: Any) -> Any:
    if isinstance(like, bool):
        return _coerce_bool(value)
    if isinstance(like, int):
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if isinstance(like, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if isinstance(like, str):
        try:
            return str(value)
        except (TypeError, ValueError):
            return value
    return value


def _coerce_bool(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
        return value
    if isinstance(value, int | float) and value in {0, 1}:
        return bool(value)
    return value
