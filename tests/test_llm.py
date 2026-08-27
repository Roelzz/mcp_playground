import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import db
import llm
import service
import store
from auth import get_conn


@pytest.fixture
def client(tmp_path):
    conn = db.init_db(str(tmp_path / "test.db"))
    app = FastAPI()
    app.include_router(llm.router)
    app.dependency_overrides[get_conn] = lambda: conn
    with TestClient(app) as c:
        yield c


def _conn(client: TestClient):
    return client.app.dependency_overrides[get_conn]()


def _create_llm(client: TestClient, slug: str = "mock", **fields: Any) -> dict[str, Any]:
    defaults = {"slug": slug, "name": slug.title(), "mode": "mock"}
    defaults.update(fields)
    return service.create_llm_endpoint(_conn(client), **defaults)


def _set_responses(
    client: TestClient, llm_id: int, responses: list[dict[str, Any]]
) -> dict[str, Any]:
    return service.set_llm_responses(_conn(client), llm_id, responses)


def _post_chat(
    client: TestClient,
    slug: str,
    messages: list[dict[str, Any]] | None = None,
    **body: Any,
):
    payload = {"messages": messages or [{"role": "user", "content": "hello"}]}
    payload.update(body)
    return client.post(f"/v1/{slug}/chat/completions", json=payload)


def test_models_returns_openai_list_envelope_and_unknown_slug_404(client: TestClient) -> None:
    _create_llm(client, model_name="demo-model")

    response = client.get("/v1/mock/models")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "demo-model"
    assert body["data"][0]["object"] == "model"
    assert isinstance(body["data"][0]["created"], int)
    assert body["data"][0]["owned_by"] == "mcp-playground"
    assert client.get("/v1/missing/models").status_code == 404


def test_mock_always_rule_returns_completion_text_and_usage(client: TestClient) -> None:
    endpoint = _create_llm(client)
    _set_responses(client, endpoint["id"], [{"match_type": "always", "response": "Always wins"}])

    response = _post_chat(client, "mock", [{"role": "user", "content": "anything"}])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "Always wins"
    usage = body["usage"]
    assert usage["prompt_tokens"] + usage["completion_tokens"] == usage["total_tokens"]


def test_contains_matching_is_case_insensitive(client: TestClient) -> None:
    endpoint = _create_llm(client)
    _set_responses(
        client,
        endpoint["id"],
        [{"match_type": "contains", "match_value": "ALPHA", "response": "contains hit"}],
    )

    response = _post_chat(client, "mock", [{"role": "user", "content": "tell me about alpha"}])

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "contains hit"


def test_regex_matching_works_and_invalid_regex_is_skipped(client: TestClient) -> None:
    endpoint = _create_llm(client)
    _set_responses(
        client,
        endpoint["id"],
        [
            {"match_type": "regex", "match_value": "(", "response": "bad regex"},
            {"match_type": "regex", "match_value": r"ticket-\d+", "response": "regex hit"},
        ],
    )

    response = _post_chat(client, "mock", [{"role": "user", "content": "check TICKET-42"}])

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "regex hit"


def test_ordinal_ordering_lower_ordinal_wins(client: TestClient) -> None:
    endpoint = _create_llm(client)
    _set_responses(
        client,
        endpoint["id"],
        [
            {"match_type": "contains", "match_value": "demo", "response": "first"},
            {"match_type": "contains", "match_value": "demo", "response": "second"},
        ],
    )

    response = _post_chat(client, "mock", [{"role": "user", "content": "demo"}])

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "first"


def test_no_rules_returns_friendly_fallback(client: TestClient) -> None:
    _create_llm(client)

    response = _post_chat(client, "mock")

    assert response.status_code == 200, response.text
    assert "No response rule matched" in response.json()["choices"][0]["message"]["content"]
    assert "'mock'" in response.json()["choices"][0]["message"]["content"]


def test_probe_extraction_supports_content_part_lists(client: TestClient) -> None:
    endpoint = _create_llm(client)
    _set_responses(
        client,
        endpoint["id"],
        [{"match_type": "contains", "match_value": "weather", "response": "list content hit"}],
    )

    response = _post_chat(
        client,
        "mock",
        [{"role": "user", "content": [{"type": "text", "text": "What is the weather?"}]}],
    )

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "list content hit"


def test_extra_fields_and_authorization_header_do_not_break_request(client: TestClient) -> None:
    endpoint = _create_llm(client)
    _set_responses(client, endpoint["id"], [{"match_type": "always", "response": "ok"}])

    response = client.post(
        "/v1/mock/chat/completions",
        headers={"Authorization": "Bearer sk-test"},
        json={
            "messages": [{"role": "user", "content": "hello"}],
            "top_p": 0.8,
            "n": 1,
            "tools": [{"type": "function", "function": {"name": "noop"}}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "ok"


def test_mock_streaming_uses_openai_delta_wire_format(client: TestClient) -> None:
    endpoint = _create_llm(client)
    _set_responses(
        client,
        endpoint["id"],
        [{"match_type": "always", "response": "Hello streaming world"}],
    )

    response = _post_chat(client, "mock", stream=True)

    assert response.status_code == 200, response.text
    assert "text/event-stream" in response.headers["content-type"]
    assert "chat.completion.chunk" in response.text
    assert response.text.endswith("data: [DONE]\n\n")

    reconstructed = ""
    for line in response.text.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        chunk = json.loads(line.removeprefix("data: "))
        reconstructed += chunk["choices"][0]["delta"].get("content", "")
    assert reconstructed == "Hello streaming world"


def test_proxy_mode_with_empty_upstream_url_returns_400(client: TestClient) -> None:
    endpoint = _create_llm(
        client,
        slug="proxy",
        mode="proxy",
        upstream_url="https://example.invalid/v1/chat/completions",
    )
    service.update_llm_endpoint(_conn(client), endpoint["id"], upstream_url="")

    response = _post_chat(client, "proxy")

    assert response.status_code == 400
    assert "upstream_url" in response.json()["detail"]


def test_proxy_mode_success_forwards_request_and_returns_upstream_json(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

        async def post(
            self, url: str, headers: dict[str, str], json: dict[str, Any]
        ) -> httpx.Response:
            calls.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
            return httpx.Response(
                200,
                json={
                    "id": "upstream",
                    "choices": [{"message": {"content": "proxied"}}],
                },
                headers={"content-type": "application/json"},
            )

    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeAsyncClient)
    _create_llm(
        client,
        slug="proxy",
        mode="proxy",
        model_name="proxy-model",
        upstream_url="https://example.openai.azure.com",
        upstream_key="secret",
        upstream_deployment="deployment-a",
        system_prompt="Configured system prompt.",
    )

    response = _post_chat(
        client,
        "proxy",
        [{"role": "system", "content": "caller system"}, {"role": "user", "content": "hi"}],
        temperature=0.2,
        max_tokens=32,
        top_p=0.9,
    )

    assert response.status_code == 200, response.text
    assert response.json()["id"] == "upstream"
    assert calls[0]["timeout"] == 60.0
    assert calls[0]["url"] == (
        "https://example.openai.azure.com/openai/deployments/deployment-a/"
        "chat/completions?api-version=2024-10-21"
    )
    assert calls[0]["headers"]["api-key"] == "secret"
    payload = calls[0]["json"]
    assert payload["model"] == "proxy-model"
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 32
    assert payload["top_p"] == 0.9
    assert payload["messages"][0] == {"role": "system", "content": "Configured system prompt."}
    assert payload["messages"][1] == {"role": "system", "content": "caller system"}


def test_proxy_mode_non_2xx_returns_upstream_status_and_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self) -> "FakeAsyncClient":
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
            return None

        async def post(
            self, url: str, headers: dict[str, str], json: dict[str, Any]
        ) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "bad key"}})

    monkeypatch.setattr(llm.httpx, "AsyncClient", FakeAsyncClient)
    _create_llm(
        client,
        slug="proxy",
        mode="proxy",
        upstream_url="https://api.example.test/v1/chat/completions",
        upstream_key="secret",
    )

    response = _post_chat(client, "proxy")

    assert response.status_code == 401
    assert response.json()["detail"] == {"error": {"message": "bad key"}}


def _mint_key(client: TestClient, label: str = "trainee", scope: str = "admin") -> str:
    key = auth.generate_api_key()
    store.create_api_key(_conn(client), label=label, key_hash=auth.hash_api_key(key), scope=scope)
    return key


def test_auth_mode_none_leaves_endpoint_open(client: TestClient) -> None:
    _create_llm(client, auth_mode="none")

    assert client.get("/v1/mock/models").status_code == 200
    assert _post_chat(client, "mock").status_code == 200


def test_auth_mode_api_key_rejects_anonymous_callers(client: TestClient) -> None:
    _create_llm(client, auth_mode="api_key")

    models = client.get("/v1/mock/models")
    assert models.status_code == 401
    assert models.headers["WWW-Authenticate"] == "Bearer"
    assert "mock" in models.json()["detail"]

    chat = _post_chat(client, "mock")
    assert chat.status_code == 401

    logged = store.get_traffic(_conn(client))
    assert any(row["status"] == "unauthorized" for row in logged)


def test_auth_mode_api_key_rejects_unknown_key(client: TestClient) -> None:
    _create_llm(client, auth_mode="api_key")

    response = client.get("/v1/mock/models", headers={"Authorization": "Bearer mcpp_nope"})

    assert response.status_code == 401


def test_auth_mode_api_key_accepts_bearer_and_x_api_key(client: TestClient) -> None:
    _create_llm(client, auth_mode="api_key")
    key = _mint_key(client)

    bearer = client.get("/v1/mock/models", headers={"Authorization": f"Bearer {key}"})
    assert bearer.status_code == 200, bearer.text

    header = client.get("/v1/mock/models", headers={"X-API-Key": key})
    assert header.status_code == 200, header.text

    chat = client.post(
        "/v1/mock/chat/completions",
        json={"messages": [{"role": "user", "content": "hello"}]},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert chat.status_code == 200, chat.text


def test_readonly_key_may_still_call_a_protected_llm_endpoint(client: TestClient) -> None:
    _create_llm(client, auth_mode="api_key")
    key = _mint_key(client, label="ro", scope="readonly")

    response = client.get("/v1/mock/models", headers={"Authorization": f"Bearer {key}"})

    assert response.status_code == 200, response.text
