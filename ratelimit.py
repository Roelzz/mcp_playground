"""Per-caller request throttling for the participant-facing surfaces.

Implemented as raw ASGI rather than a BaseHTTPMiddleware subclass: the /mcp
transport streams Server-Sent Events, and BaseHTTPMiddleware wraps the response
body in a way that interferes with long-lived streams.
"""

import os
import time
from collections import deque
from http.cookies import SimpleCookie
from threading import Lock

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

WINDOW_SECONDS = 60
DEFAULT_LIMIT = 300
DEFAULT_ANON_LIMIT = 6000
GUARDED_PREFIXES = ("/mcp", "/v1", "/rest", "/api")
SESSION_COOKIE = "pg_session"

_HITS: dict[str, deque[float]] = {}
_LOCK = Lock()


def _limit_from_env(name: str, fallback: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def limit_per_minute() -> int:
    """Budget for a caller identified by API key or session cookie."""
    return _limit_from_env("RATE_LIMIT_PER_MINUTE", DEFAULT_LIMIT)


def anon_limit_per_minute() -> int:
    """Budget for a caller identified only by IP address.

    Deliberately far higher. Seeded servers run with ``auth_mode: none`` and
    attendees reach them through Copilot Studio, whose requests arrive from a
    small pool of shared Microsoft egress addresses. A whole cohort therefore
    lands in one bucket, and a per-key-sized budget would throttle the class
    rather than an abuser.
    """
    return _limit_from_env("RATE_LIMIT_PER_MINUTE_ANON", DEFAULT_ANON_LIMIT)


def reset() -> None:
    with _LOCK:
        _HITS.clear()


def _now() -> float:
    return time.monotonic()


def _header(headers: list[tuple[bytes, bytes]], name: bytes) -> str:
    for key, value in headers:
        if key.lower() == name:
            return value.decode("latin-1")
    return ""


def _caller_id(scope: Scope) -> str:
    """Identify by API key when present, then session cookie, then client address."""
    headers = scope.get("headers") or []

    scheme, _, credential = _header(headers, b"authorization").partition(" ")
    if scheme.lower() == "bearer" and credential.strip():
        return f"key:{credential.strip()}"

    api_key = _header(headers, b"x-api-key").strip()
    if api_key:
        return f"key:{api_key}"

    raw_cookie = _header(headers, b"cookie")
    if raw_cookie:
        jar = SimpleCookie()
        jar.load(raw_cookie)
        morsel = jar.get(SESSION_COOKIE)
        if morsel is not None and morsel.value:
            return f"session:{morsel.value}"

    client = scope.get("client")
    return f"ip:{client[0] if client else 'unknown'}"


def check(scope: Scope) -> int | None:
    """Record a hit. Returns Retry-After seconds when the caller is over budget."""
    caller = _caller_id(scope)
    limit = anon_limit_per_minute() if caller.startswith("ip:") else limit_per_minute()
    if limit <= 0:
        return None

    now = _now()
    with _LOCK:
        hits = _HITS.setdefault(caller, deque())
        while hits and now - hits[0] >= WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            return max(1, int(WINDOW_SECONDS - (now - hits[0])) + 1)
        hits.append(now)
    return None


def guarded(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in GUARDED_PREFIXES)


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not guarded(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        retry_after = check(scope)
        if retry_after is None:
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            status_code=429,
            content={"detail": "rate limit exceeded, slow down"},
            headers={"Retry-After": str(retry_after)},
        )
        await response(scope, receive, send)
