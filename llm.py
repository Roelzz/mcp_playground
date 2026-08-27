"""OpenAI-compatible LLM endpoint adapter."""

import json
import re
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse, Response, StreamingResponse

import auth
import service
import store

router = APIRouter(prefix="/v1", tags=["llm"])

STATUS = {"not_found": 404, "conflict": 409, "invalid": 400}
Conn = auth.ConnDep


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str | None = None
    messages: list[dict[str, Any]]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None


def _handle(fn, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except service.ServiceError as exc:
        raise HTTPException(status_code=STATUS.get(exc.code, 400), detail=exc.message) from exc


def _require_endpoint_auth(
    request: Request, conn: sqlite3.Connection, llm: dict[str, Any]
) -> None:
    """Enforce the endpoint's own auth_mode, mirroring the /mcp/{slug} gate in main.py."""
    if llm.get("auth_mode") != "api_key":
        return
    key = auth._request_api_key(request)
    principal = auth._resolve_api_key(conn, key) if key else None
    if principal is None:
        raise HTTPException(
            status_code=401,
            detail=f"api key required for llm endpoint: {llm['slug']}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _now() -> int:
    return int(time.time())


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ""


def _probe_from_messages(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return _extract_text(message.get("content"))
    return ""


def _mock_response(slug: str, responses: list[dict[str, Any]], probe: str) -> tuple[str, bool]:
    for response in responses:
        match_type = response["match_type"]
        match_value = response.get("match_value") or ""
        if match_type == "always":
            return response["response"], True
        if match_type == "contains" and match_value.lower() in probe.lower():
            return response["response"], True
        if match_type == "regex":
            try:
                if re.search(match_value, probe, re.IGNORECASE):
                    return response["response"], True
            except re.error as exc:
                logger.warning(
                    f"skipping invalid regex for llm endpoint {slug!r}: {match_value!r} ({exc})"
                )
    return (
        f"[mock] No response rule matched. Configure responses for the {slug!r} endpoint "
        "in MCP Playground.",
        False,
    )


def _completion_envelope(model: str, text: str, prompt: str) -> dict[str, Any]:
    prompt_tokens = _estimate_tokens(prompt)
    completion_tokens = _estimate_tokens(text)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": _now(),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _json_line(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode()


def _text_chunks(text: str, size: int = 5) -> list[str]:
    parts = re.findall(r"\S+\s*", text)
    if not parts and text:
        return [text]
    return ["".join(parts[i : i + size]) for i in range(0, len(parts), size)]


async def _mock_stream(model: str, text: str) -> AsyncIterator[bytes]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = _now()
    base = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
    }
    yield _json_line(
        {
            **base,
            "choices": [
                {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
            ],
        }
    )
    for chunk in _text_chunks(text):
        yield _json_line(
            {
                **base,
                "choices": [
                    {"index": 0, "delta": {"content": chunk}, "finish_reason": None}
                ],
            }
        )
    yield _json_line(
        {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    )
    yield b"data: [DONE]\n\n"


def _proxy_target(llm: dict[str, Any]) -> tuple[str, dict[str, str]]:
    upstream_url = llm.get("upstream_url")
    if not upstream_url:
        raise HTTPException(
            status_code=400,
            detail="proxy mode requires upstream_url to be configured for this endpoint",
        )

    headers = {"Content-Type": "application/json"}
    upstream_key = llm.get("upstream_key") or ""
    deployment = llm.get("upstream_deployment")
    if deployment:
        # Azure OpenAI deploys chat completions under the deployment-specific path.
        url = (
            f"{upstream_url.rstrip('/')}/openai/deployments/{deployment}"
            "/chat/completions?api-version=2024-10-21"
        )
        headers["api-key"] = upstream_key
        return url, headers

    headers["Authorization"] = f"Bearer {upstream_key}"
    return upstream_url, headers


def _proxy_payload(llm: dict[str, Any], body: ChatCompletionRequest) -> dict[str, Any]:
    payload = body.model_dump(exclude_none=True)
    messages = list(body.messages)
    if llm.get("system_prompt"):
        messages = [{"role": "system", "content": llm["system_prompt"]}, *messages]
    payload["messages"] = messages
    payload["model"] = body.model or llm["model_name"]
    return payload


def _upstream_detail(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


async def _proxy_completion(
    llm: dict[str, Any], body: ChatCompletionRequest
) -> dict[str, Any] | Response:
    url, headers = _proxy_target(llm)
    payload = _proxy_payload(llm, body)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, headers=headers, json=payload)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=502, detail="upstream request timed out") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"upstream request failed: {exc}") from exc

    if response.status_code < 200 or response.status_code >= 300:
        raise HTTPException(status_code=response.status_code, detail=_upstream_detail(response))

    try:
        return JSONResponse(status_code=response.status_code, content=response.json())
    except ValueError:
        return Response(
            content=response.content,
            status_code=response.status_code,
            media_type=response.headers.get("content-type"),
        )


async def _proxy_stream(llm: dict[str, Any], body: ChatCompletionRequest) -> StreamingResponse:
    url, headers = _proxy_target(llm)
    payload = _proxy_payload(llm, body)
    client = httpx.AsyncClient(timeout=60.0)
    request = client.build_request("POST", url, headers=headers, json=payload)
    try:
        response = await client.send(request, stream=True)
    except httpx.TimeoutException as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail="upstream request timed out") from exc
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"upstream request failed: {exc}") from exc

    if response.status_code < 200 or response.status_code >= 300:
        content = await response.aread()
        await response.aclose()
        await client.aclose()
        try:
            detail = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = content.decode("utf-8", errors="replace")
        raise HTTPException(status_code=response.status_code, detail=detail)

    async def upstream_bytes() -> AsyncIterator[bytes]:
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    content_type = response.headers.get("content-type")
    headers_out = {"content-type": content_type} if content_type else None
    return StreamingResponse(
        upstream_bytes(),
        headers=headers_out,
        media_type=None if content_type else "text/event-stream",
    )


@router.get("/{slug}/models")
def list_models(
    slug: str,
    request: Request,
    conn: sqlite3.Connection = Conn,
) -> dict[str, Any]:
    llm = _handle(service.get_llm_endpoint_by_slug, conn, slug)
    _require_endpoint_auth(request, conn, llm)
    return {
        "object": "list",
        "data": [
            {
                "id": llm["model_name"],
                "object": "model",
                "created": _now(),
                "owned_by": "mcp-playground",
            }
        ],
    }


def _log_llm(
    conn: sqlite3.Connection,
    slug: str,
    started: float,
    status: str,
    request: dict[str, Any],
    response: Any,
) -> None:
    store.log_call(
        conn,
        kind="llm",
        status=status,
        target_slug=slug,
        tool_name="chat/completions",
        request=request,
        response=response,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


@router.post("/{slug}/chat/completions", response_model=None)
async def chat_completions(
    slug: str,
    body: ChatCompletionRequest,
    request: Request,
    conn: sqlite3.Connection = Conn,
) -> dict[str, Any] | Response:
    started = time.perf_counter()
    logged = {"model": body.model, "stream": body.stream, "messages": body.messages}
    try:
        llm = _handle(service.get_llm_endpoint_by_slug, conn, slug)
    except HTTPException as exc:
        _log_llm(conn, slug, started, "not_found", logged, {"error": exc.detail})
        raise
    try:
        _require_endpoint_auth(request, conn, llm)
    except HTTPException as exc:
        _log_llm(conn, slug, started, "unauthorized", logged, {"error": exc.detail})
        raise
    matched = False

    if llm["mode"] == "mock":
        llm = _handle(service.get_llm_endpoint, conn, llm["id"])
        probe = _probe_from_messages(body.messages)
        text, matched = _mock_response(slug, llm["responses"], probe)
        logger.info(
            f"llm completion slug={slug!r} mode={llm['mode']!r} "
            f"stream={body.stream} rule_matched={matched}"
        )
        _log_llm(conn, slug, started, "ok", logged, {"text": text, "rule_matched": matched})
        if body.stream:
            return StreamingResponse(
                _mock_stream(llm["model_name"], text),
                media_type="text/event-stream",
            )
        return _completion_envelope(llm["model_name"], text, probe)

    logger.info(
        f"llm completion slug={slug!r} mode={llm['mode']!r} "
        f"stream={body.stream} rule_matched={matched}"
    )
    try:
        if body.stream:
            result = await _proxy_stream(llm, body)
            _log_llm(conn, slug, started, "ok", logged, {"proxy": "stream"})
            return result
        result = await _proxy_completion(llm, body)
        _log_llm(conn, slug, started, "ok", logged, {"proxy": "completion"})
        return result
    except HTTPException as exc:
        _log_llm(conn, slug, started, str(exc.status_code), logged, {"error": exc.detail})
        raise
