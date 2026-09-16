"""Rate limiting behaviour for the participant-facing surfaces."""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import ratelimit

KEY = {"X-API-Key": "trainer-key"}


@pytest.fixture(autouse=True)
def clean_buckets(monkeypatch):
    monkeypatch.delenv("RATE_LIMIT_PER_MINUTE", raising=False)
    monkeypatch.delenv("RATE_LIMIT_PER_MINUTE_ANON", raising=False)
    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture
def client():
    async def ok(_request):
        return PlainTextResponse("ok")

    app = Starlette(
        routes=[
            Route("/v1/{rest:path}", ok),
            Route("/mcp/{rest:path}", ok),
            Route("/health", ok),
            Route("/ui/{rest:path}", ok),
        ]
    )
    app.add_middleware(ratelimit.RateLimitMiddleware)
    return TestClient(app)


# ── Identified callers ────────────────────────────────────────────────────────


def test_requests_under_the_limit_pass(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "3")
    for _ in range(3):
        assert client.get("/v1/demo/models", headers=KEY).status_code == 200


def test_request_over_the_limit_is_rejected(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    client.get("/v1/demo/models", headers=KEY)
    client.get("/v1/demo/models", headers=KEY)

    response = client.get("/v1/demo/models", headers=KEY)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert "rate limit" in response.json()["detail"]


def test_budget_is_per_api_key(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    assert client.get("/mcp/alpha", headers={"X-API-Key": "one"}).status_code == 200
    assert client.get("/mcp/alpha", headers={"X-API-Key": "two"}).status_code == 200
    assert client.get("/mcp/alpha", headers={"X-API-Key": "one"}).status_code == 429


def test_bearer_and_x_api_key_share_one_budget(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    assert client.get("/mcp/alpha", headers={"Authorization": "Bearer k"}).status_code == 200
    assert client.get("/mcp/alpha", headers={"X-API-Key": "k"}).status_code == 429


def test_session_cookie_identifies_the_caller(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    client.cookies.set(ratelimit.SESSION_COOKIE, "abc")
    assert client.get("/v1/demo/models").status_code == 200
    assert client.get("/v1/demo/models").status_code == 429


# ── Anonymous callers share an egress IP, so they get their own budget ────────


def test_anonymous_callers_use_the_anon_budget(client, monkeypatch):
    """A whole cohort reaches auth_mode=none servers from one shared egress IP."""
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE_ANON", "5")

    for _ in range(5):
        assert client.get("/mcp/open-server").status_code == 200
    assert client.get("/mcp/open-server").status_code == 429


def test_anon_budget_defaults_far_above_the_keyed_budget():
    assert ratelimit.anon_limit_per_minute() > ratelimit.limit_per_minute()
    assert ratelimit.anon_limit_per_minute() == ratelimit.DEFAULT_ANON_LIMIT


def test_keyed_caller_is_unaffected_by_a_saturated_anon_bucket(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "5")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE_ANON", "1")

    assert client.get("/mcp/alpha").status_code == 200
    assert client.get("/mcp/alpha").status_code == 429
    assert client.get("/mcp/alpha", headers=KEY).status_code == 200


# ── Scope and configuration ───────────────────────────────────────────────────


def test_unguarded_paths_are_never_throttled(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE_ANON", "1")
    for _ in range(5):
        assert client.get("/health").status_code == 200
        assert client.get("/ui/index.html").status_code == 200


def test_zero_disables_throttling(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "0")
    for _ in range(50):
        assert client.get("/v1/demo/models", headers=KEY).status_code == 200


def test_zero_disables_throttling_for_anonymous_callers(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE_ANON", "0")
    for _ in range(50):
        assert client.get("/v1/demo/models").status_code == 200


def test_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "not-a-number")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE_ANON", "")
    assert ratelimit.limit_per_minute() == ratelimit.DEFAULT_LIMIT
    assert ratelimit.anon_limit_per_minute() == ratelimit.DEFAULT_ANON_LIMIT


def test_window_slides_so_callers_recover(client, monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "1")
    clock = [1000.0]
    monkeypatch.setattr(ratelimit, "_now", lambda: clock[0])

    assert client.get("/v1/demo/models", headers=KEY).status_code == 200
    assert client.get("/v1/demo/models", headers=KEY).status_code == 429

    clock[0] += ratelimit.WINDOW_SECONDS + 1
    assert client.get("/v1/demo/models", headers=KEY).status_code == 200


def test_guarded_prefix_matching_is_exact(client):
    assert ratelimit.guarded("/mcp")
    assert ratelimit.guarded("/mcp/alpha")
    assert not ratelimit.guarded("/mcpsomething")
    assert not ratelimit.guarded("/health")
