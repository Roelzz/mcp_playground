"""Smoke-tests for the real application composition root in main.py.

Every test imports ``main.app`` directly, drives it through a TestClient backed
by an isolated tmp database, and issues a real HTTP request.  A 404 means the
surface is not mounted; we assert that it is NOT 404 (or check the exact code
where it is predictable and stable).
"""

import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    # DB_PATH is consumed at lifespan startup and on every request, not at import
    # time, so setting it here — before TestClient.__enter__ triggers the lifespan
    # — is sufficient.  No importlib.reload needed.
    monkeypatch.setenv("DB_PATH", str(tmp_path / "wiring.db"))
    with TestClient(main.app, raise_server_exceptions=False) as c:
        yield c


# ── Open surfaces ─────────────────────────────────────────────────────────────


def test_health_is_mounted_and_open(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_redirects_to_ui(client: TestClient) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code in {301, 302, 307, 308}
    assert "/ui" in r.headers["location"]


def test_ui_spa_shell_is_mounted(client: TestClient) -> None:
    r = client.get("/ui/")
    assert r.status_code == 200


# ── Admin CRUD API ────────────────────────────────────────────────────────────


def test_admin_api_is_mounted_and_auth_gate_is_wired(client: TestClient) -> None:
    """Unauthenticated GET /api/servers must be 401 — not 404 and not open."""
    r = client.get("/api/servers")
    assert r.status_code == 401


# ── Plain REST mock ───────────────────────────────────────────────────────────


def test_mock_rest_router_is_mounted(client: TestClient) -> None:
    """contoso-orders is seeded with auth_mode='none'; listing orders must not 404."""
    r = client.get("/mock/contoso-orders/orders")
    assert r.status_code != 404


def test_mock_rest_unknown_slug_returns_404(client: TestClient) -> None:
    """A slug that was never created should produce a 404 from the handler, not a 500."""
    r = client.get("/mock/no-such-server/orders")
    assert r.status_code == 404


# ── Mock MCP ──────────────────────────────────────────────────────────────────


def test_mcp_server_route_is_mounted(client: TestClient) -> None:
    """POST /mcp/contoso-orders with an empty body must not 404.
    The MCP protocol rejects a missing body with 400, which proves the route is there."""
    r = client.post("/mcp/contoso-orders")
    assert r.status_code != 404


# ── Management MCP ────────────────────────────────────────────────────────────


def test_mcp_admin_is_mounted_and_auth_gate_is_wired(client: TestClient) -> None:
    """Reaching /mcp/_admin without an admin API key must be 401, not 404."""
    r = client.post("/mcp/_admin")
    assert r.status_code == 401


# ── OpenAI-compatible chat ────────────────────────────────────────────────────


def test_llm_chat_completions_route_is_mounted(client: TestClient) -> None:
    """GET on a POST-only route returns 405 (method not allowed), proving it IS mounted.
    A 404 would mean the route is absent."""
    r = client.get("/v1/demo-llm/chat/completions")
    assert r.status_code == 405


def test_llm_chat_completions_handles_valid_request(client: TestClient) -> None:
    """demo-llm is seeded as a mock endpoint with auth_mode='none' and an always-match rule."""
    r = client.post(
        "/v1/demo-llm/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
