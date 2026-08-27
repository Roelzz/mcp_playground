"""MCP Playground - persistent mock MCP servers and OpenAI-compatible endpoints."""

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from loguru import logger
from starlette.routing import get_route_path

import api
import db
import mcp_builder
import seed
import service

load_dotenv()

logger.remove()
logger.add(
    sink=lambda msg: print(msg, end="", flush=True),
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="{time:DD-MM-YYYY at HH:mm:ss} | {level: <8} | {message}",
)

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    logger.info(f"database ready at {db.db_path()}")
    with db.connect() as conn:
        if seed.seed_if_empty(conn):
            logger.info("seeded demo content")
    yield


app = FastAPI(
    title="MCP Playground",
    description="Author mock MCP servers and OpenAI-compatible endpoints for Copilot Studio.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(api.router)


async def mcp_asgi(scope: dict, receive, send) -> None:
    """Raw ASGI entry point for /mcp/{slug} Streamable HTTP transports."""
    slug = get_route_path(scope).lstrip("/").split("/")[0]
    with db.connect() as conn:
        try:
            service.get_server_by_slug(conn, slug)
        except service.NotFound:
            await PlainTextResponse(f"unknown mcp server: {slug}", status_code=404)(
                scope, receive, send
            )
            return
        await mcp_builder.dispatch(conn, slug, scope, receive, send)


app.mount("/mcp", mcp_asgi)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def main() -> None:
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "2009"))
    logger.info(f"Starting MCP Playground on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_config=None)


if __name__ == "__main__":
    sys.exit(main())
