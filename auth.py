"""Auth seam.

Cut 1a ships an allow-all `require_admin` so the dependency wiring exists from
day one. Cut 1b swaps the body for real session/API-key checks without touching
a single route.
"""

import sqlite3
from typing import Any

from fastapi import Depends, Request

import db


def get_conn() -> sqlite3.Connection:
    """Per-request connection. SQLite connections are cheap; WAL handles concurrency."""
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


class Principal:
    """Who is making the call. Cut 1a always returns the same anonymous admin."""

    def __init__(self, label: str = "anonymous", scope: str = "admin") -> None:
        self.label = label
        self.scope = scope

    @property
    def can_write(self) -> bool:
        return self.scope == "admin"

    def __repr__(self) -> str:
        return f"Principal(label={self.label!r}, scope={self.scope!r})"


async def require_admin(request: Request) -> Principal:
    """Allow-all stub. The seam is real; the check is not, yet."""
    return Principal()


AdminDep = Depends(require_admin)
ConnDep = Depends(get_conn)


def principal_from_request(request: Request) -> Any:
    return getattr(request.state, "principal", Principal())
