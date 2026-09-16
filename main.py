"""MCP Playground - persistent mock MCP servers and OpenAI-compatible endpoints."""

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import get_route_path

import api
import auth
import db
import llm
import mcp_builder
import portability
import ratelimit
import recipes
import rest
import seed
import service
from admin_mcp import build_admin_server

load_dotenv()

_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

logger.remove()
logger.add(
    sink=lambda msg: print(msg, end="", flush=True),
    level=os.getenv("LOG_LEVEL") or "INFO",
    format="{time:DD-MM-YYYY at HH:mm:ss} | {level: <8} | {message}",
)

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await run_in_threadpool(_init_application_db)
    yield


def _init_application_db() -> None:
    with closing(db.init_db()) as conn:
        seeded = seed.seed_if_empty(conn)
        auth.bootstrap(conn)
    logger.info(f"database ready at {db.db_path()}")
    if seeded:
        logger.info("seeded demo content")


app = FastAPI(
    title="MCP Playground",
    description="Author mock MCP servers and OpenAI-compatible endpoints for Copilot Studio.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(ratelimit.RateLimitMiddleware)

app.include_router(auth.router)
app.include_router(api.router)
app.include_router(llm.router)
app.include_router(portability.router)
app.include_router(recipes.router)
app.include_router(rest.router)


def _unauthorized(message: str):
    return PlainTextResponse(
        message, status_code=401, headers={"WWW-Authenticate": "Bearer"}
    )


def _principal_for(conn, scope: dict) -> auth.Principal | None:
    key = auth._request_api_key(Request(scope))
    if key is None:
        return None
    return auth._resolve_api_key(conn, key)


async def _dispatch_admin(conn, scope: dict, receive, send) -> None:
    """Serve the management MCP server at /mcp/_admin behind an admin API key."""
    mcp = await run_in_threadpool(build_admin_server, conn)
    mcp.streamable_http_app()
    async with mcp.session_manager.run():
        await mcp.session_manager.handle_request(scope, receive, send)


async def mcp_asgi(scope: dict, receive, send) -> None:
    """Raw ASGI entry point for /mcp/{slug} Streamable HTTP transports."""
    slug = get_route_path(scope).lstrip("/").split("/")[0]
    with closing(db.connect()) as conn:
        if slug == "_admin":
            principal = await run_in_threadpool(_principal_for, conn, scope)
            if principal is None:
                await _unauthorized("admin api key required")(scope, receive, send)
                return
            if principal.scope != "admin":
                await PlainTextResponse("admin scope required", status_code=403)(
                    scope, receive, send
                )
                return
            await _dispatch_admin(conn, scope, receive, send)
            return

        try:
            server = await run_in_threadpool(service.get_server_by_slug, conn, slug)
        except service.NotFound:
            await PlainTextResponse(f"unknown mcp server: {slug}", status_code=404)(
                scope, receive, send
            )
            return
        if server.get("auth_mode") == "api_key" and (
            await run_in_threadpool(_principal_for, conn, scope)
        ) is None:
            await _unauthorized(f"api key required for mcp server: {slug}")(scope, receive, send)
            return
        await mcp_builder.dispatch(conn, slug, scope, receive, send)


app.mount("/mcp", mcp_asgi)
app.mount("/ui", StaticFiles(directory=_STATIC_DIR, html=True), name="ui")


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/ui/")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    import uvicorn

    host = os.getenv("HOST") or "0.0.0.0"
    port = int(os.getenv("PORT") or "2009")
    logger.info(f"Starting MCP Playground on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_config=None)


if __name__ == "__main__":
    sys.exit(main())
