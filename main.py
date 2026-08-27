"""MCP Playground - persistent mock MCP servers and OpenAI-compatible endpoints."""

import os
import sys

from dotenv import load_dotenv
from fastapi import FastAPI
from loguru import logger

load_dotenv()

logger.remove()
logger.add(
    sink=lambda msg: print(msg, end="", flush=True),
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="{time:DD-MM-YYYY at HH:mm:ss} | {level: <8} | {message}",
)

app = FastAPI(
    title="MCP Playground",
    description="Author mock MCP servers and OpenAI-compatible endpoints for Copilot Studio.",
    version="0.1.0",
)


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
