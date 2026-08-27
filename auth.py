"""Authentication helpers and HTTP routes."""

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from collections.abc import Iterator
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from loguru import logger
from pydantic import BaseModel, Field

import db

SESSION_COOKIE = "pg_session"
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
HASH_BYTES = 32
LOCKOUT_WINDOW_SECONDS = 60
LOCKOUT_FAILURES = 5

_SESSIONS: dict[str, dict[str, Any]] = {}
_LOGIN_FAILURES: dict[str, list[float]] = {}

Scope = Literal["admin", "readonly"]


def get_conn() -> Iterator[sqlite3.Connection]:
    """Per-request connection. SQLite connections are cheap; WAL handles concurrency."""
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


class Principal:
    def __init__(self, label: str = "anonymous", scope: str = "admin") -> None:
        self.label = label
        self.scope = scope

    @property
    def can_write(self) -> bool:
        return self.scope == "admin"

    def __repr__(self) -> str:
        return f"Principal(label={self.label!r}, scope={self.scope!r})"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class KeyCreateRequest(BaseModel):
    label: str = Field(min_length=1)
    scope: Scope = "admin"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=HASH_BYTES,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n_raw, r_raw, p_raw, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_raw),
            r=int(r_raw),
            p=int(p_raw),
            dklen=len(expected),
        )
    except (TypeError, ValueError, OverflowError):
        return False
    return hmac.compare_digest(digest, expected)


def generate_api_key() -> str:
    return f"mcpp_{secrets.token_urlsafe(32)}"


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _session_ttl_seconds() -> int:
    try:
        hours = float(os.getenv("SESSION_TTL_HOURS", "12"))
    except ValueError:
        hours = 12.0
    return int(hours * 3600)


def create_session(username: str, scope: str = "admin") -> str:
    token = secrets.token_urlsafe(32)
    _SESSIONS[token] = {
        "username": username,
        "scope": scope,
        "expires_at": time.time() + _session_ttl_seconds(),
    }
    return token


def resolve_session(token: str) -> Principal | None:
    session = _SESSIONS.get(token)
    if session is None:
        return None
    if float(session["expires_at"]) <= time.time():
        destroy_session(token)
        return None
    return Principal(label=str(session["username"]), scope=str(session["scope"]))


def destroy_session(token: str) -> None:
    _SESSIONS.pop(token, None)


def bootstrap(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT id FROM admin_user LIMIT 1").fetchone()
    if row is not None:
        return

    username = (os.getenv("BOOTSTRAP_USER") or "").strip() or "admin"
    password = (os.getenv("BOOTSTRAP_PASSWORD") or "").strip()
    if not password:
        password = secrets.token_urlsafe(16)
        logger.warning(
            "\n"
            "============================================================\n"
            f"bootstrap admin credentials: {username} / {password}\n"
            "store this now, it will not be shown again\n"
            "============================================================"
        )

    with db.transaction(conn):
        conn.execute(
            "INSERT INTO admin_user (username, password_hash) VALUES (?, ?)",
            (username, hash_password(password)),
        )


def _auth_disabled() -> bool:
    return os.getenv("AUTH_DISABLED") == "1"


def _secure_cookie() -> bool:
    return os.getenv("INSECURE_COOKIES") != "1"


def _unauthenticated() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail="not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _request_api_key(request: Request) -> str | None:
    auth_header = request.headers.get("Authorization")
    if auth_header:
        scheme, _, credential = auth_header.partition(" ")
        if scheme.lower() == "bearer" and credential.strip():
            return credential.strip()
    x_api_key = request.headers.get("X-API-Key")
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    return None


def _resolve_api_key(conn: sqlite3.Connection, key: str) -> Principal | None:
    row = conn.execute(
        """
        SELECT label, scope
        FROM api_key
        WHERE key_hash = ? AND revoked_at IS NULL
        """,
        (hash_api_key(key),),
    ).fetchone()
    if row is None:
        return None

    with db.transaction(conn):
        conn.execute(
            "UPDATE api_key SET last_used_at = datetime('now') WHERE key_hash = ?",
            (hash_api_key(key),),
        )
    return Principal(label=row["label"], scope=row["scope"])


def _remember_principal(request: Request, principal: Principal) -> Principal:
    request.state.principal = principal
    return principal


ConnDep = Depends(get_conn)


async def require_admin(request: Request, conn: sqlite3.Connection = ConnDep) -> Principal:
    if _auth_disabled():
        return _remember_principal(request, Principal())

    api_key = _request_api_key(request)
    if api_key is not None:
        principal = _resolve_api_key(conn, api_key)
        if principal is not None:
            return _remember_principal(request, principal)

    session_token = request.cookies.get(SESSION_COOKIE)
    if session_token is not None:
        principal = resolve_session(session_token)
        if principal is not None:
            return _remember_principal(request, principal)

    raise _unauthenticated()


AdminDep = Depends(require_admin)


async def require_write(principal: Principal = AdminDep) -> Principal:
    if not principal.can_write:
        raise HTTPException(status_code=403, detail="readonly key cannot write")
    return principal


WriteDep = Depends(require_write)


def principal_from_request(request: Request) -> Any:
    return getattr(request.state, "principal", Principal())


def _recent_failures(username: str, now: float) -> list[float]:
    failures = [
        timestamp
        for timestamp in _LOGIN_FAILURES.get(username, [])
        if now - timestamp < LOCKOUT_WINDOW_SECONDS
    ]
    _LOGIN_FAILURES[username] = failures
    return failures


def _enforce_login_lockout(username: str) -> None:
    if len(_recent_failures(username, time.time())) >= LOCKOUT_FAILURES:
        raise HTTPException(
            status_code=429,
            detail="too many failed attempts, try again in a minute",
        )


def _record_failed_login(username: str) -> None:
    now = time.time()
    failures = _recent_failures(username, now)
    failures.append(now)
    _LOGIN_FAILURES[username] = failures


def _clear_failed_logins(username: str) -> None:
    _LOGIN_FAILURES.pop(username, None)


router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/auth/login")
def login(
    body: LoginRequest, response: Response, conn: sqlite3.Connection = ConnDep
) -> dict[str, str]:
    _enforce_login_lockout(body.username)
    row = conn.execute(
        "SELECT username, password_hash FROM admin_user WHERE username = ?",
        (body.username,),
    ).fetchone()
    if row is None or not verify_password(body.password, row["password_hash"]):
        _record_failed_login(body.username)
        logger.warning(f"failed login for {body.username!r}")
        raise HTTPException(status_code=401, detail="invalid username or password")

    _clear_failed_logins(body.username)
    token = create_session(row["username"], "admin")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=_session_ttl_seconds(),
        httponly=True,
        samesite="lax",
        secure=_secure_cookie(),
    )
    logger.info(f"successful login for {row['username']!r}")
    return {"username": row["username"], "scope": "admin"}


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token is not None:
        destroy_session(token)
    response.delete_cookie(
        key=SESSION_COOKIE,
        httponly=True,
        samesite="lax",
        secure=_secure_cookie(),
    )


@router.get("/auth/me")
def me(principal: Principal = AdminDep) -> dict[str, str]:
    return {"username": principal.label, "scope": principal.scope}


@router.get("/keys")
def list_keys(
    conn: sqlite3.Connection = ConnDep, _: Principal = AdminDep
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, label, scope, created_at, last_used_at
        FROM api_key
        WHERE revoked_at IS NULL
        ORDER BY id
        """
    ).fetchall()
    return [dict(row) for row in rows]


@router.post("/keys", status_code=201)
def create_key(
    body: KeyCreateRequest,
    conn: sqlite3.Connection = ConnDep,
    _: Principal = WriteDep,
) -> dict[str, Any]:
    key = generate_api_key()
    with db.transaction(conn):
        cursor = conn.execute(
            "INSERT INTO api_key (label, key_hash, scope) VALUES (?, ?, ?)",
            (body.label, hash_api_key(key), body.scope),
        )
        key_id = int(cursor.lastrowid)
        row = conn.execute(
            "SELECT id, label, scope, created_at FROM api_key WHERE id = ?",
            (key_id,),
        ).fetchone()
    logger.info(f"created API key {body.label!r} (id={key_id}, scope={body.scope})")
    payload = dict(row)
    payload["key"] = key
    return payload


@router.delete("/keys/{key_id}", status_code=204)
def revoke_key(
    key_id: int,
    conn: sqlite3.Connection = ConnDep,
    _: Principal = WriteDep,
) -> None:
    row = conn.execute(
        "SELECT label FROM api_key WHERE id = ? AND revoked_at IS NULL",
        (key_id,),
    ).fetchone()
    if row is None:
        return

    with db.transaction(conn):
        conn.execute(
            "UPDATE api_key SET revoked_at = datetime('now') WHERE id = ?",
            (key_id,),
        )
    logger.info(f"revoked API key {row['label']!r} (id={key_id})")
