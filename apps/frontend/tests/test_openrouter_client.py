from __future__ import annotations

import json

import httpx
import pytest
from algotrading.frontend.openrouter import (
    OPENROUTER_BASE_URL,
    ChatMessage,
    OpenRouterClient,
    OpenRouterConfig,
    OpenRouterError,
)

API_KEY = "sk-or-test-secret"


def _config() -> OpenRouterConfig:
    return OpenRouterConfig(
        api_key=API_KEY,
        reasoning_model="anthropic/claude-opus-4-8",
        gloss_model="anthropic/claude-haiku-4-5",
    )


def test_complete_targets_openrouter_and_sends_key_in_header_not_body() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "bonjour"}}]},
        )

    client = OpenRouterClient(_config(), transport=httpx.MockTransport(handler))
    answer = client.complete([ChatMessage(role="user", content="salut")])

    assert answer == "bonjour"
    assert captured["url"] == f"{OPENROUTER_BASE_URL}/chat/completions"
    assert captured["auth"] == f"Bearer {API_KEY}"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "anthropic/claude-opus-4-8"
    assert body["stream"] is False
    # The key must never appear in the request body the model sees.
    assert API_KEY not in json.dumps(body)


def test_gloss_route_uses_the_cheap_model_in_the_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "x"}}]}
        )

    client = OpenRouterClient(_config(), transport=httpx.MockTransport(handler))
    client.complete([ChatMessage(role="user", content="q")], gloss=True)
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "anthropic/claude-haiku-4-5"


def test_http_error_status_becomes_openrouter_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    client = OpenRouterClient(_config(), transport=httpx.MockTransport(handler))
    with pytest.raises(OpenRouterError) as excinfo:
        client.complete([ChatMessage(role="user", content="q")])
    assert excinfo.value.status_code == 429


def test_stream_parses_sse_deltas() -> None:
    sse = (
        'data: {"choices":[{"delta":{"content":"La "}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"nappe"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sse)

    client = OpenRouterClient(_config(), transport=httpx.MockTransport(handler))
    tokens = list(client.stream([ChatMessage(role="user", content="q")]))
    assert tokens == ["La ", "nappe"]
