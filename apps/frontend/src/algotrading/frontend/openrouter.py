from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field

import httpx

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_DEFAULT_REASONING_MODEL = "anthropic/claude-opus-4-8"
_DEFAULT_GLOSS_MODEL = "anthropic/claude-haiku-4-5"
_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_MAX_TOKENS = 1024

_API_KEY_ENV = "OPENROUTER_API_KEY"
_REASONING_MODEL_ENV = "ASSISTANT_MODEL"
_GLOSS_MODEL_ENV = "ASSISTANT_GLOSS_MODEL"
_REFERER_ENV = "OPENROUTER_HTTP_REFERER"
_TITLE_ENV = "OPENROUTER_APP_TITLE"


class OpenRouterError(Exception):
    def __init__(self, detail: str, *, status_code: int | None = None) -> None:
        self.detail = detail
        self.status_code = status_code
        super().__init__(detail)


class MissingApiKeyError(OpenRouterError):
    def __init__(self) -> None:
        super().__init__(
            f"{_API_KEY_ENV} is not set; the assistant cannot reach OpenRouter"
        )


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True, slots=True)
class OpenRouterConfig:
    api_key: str | None
    reasoning_model: str
    gloss_model: str
    base_url: str = OPENROUTER_BASE_URL
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    referer: str | None = None
    app_title: str | None = None

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> OpenRouterConfig:
        env = environ if environ is not None else dict(os.environ)
        return cls(
            api_key=env.get(_API_KEY_ENV) or None,
            reasoning_model=env.get(_REASONING_MODEL_ENV) or _DEFAULT_REASONING_MODEL,
            gloss_model=env.get(_GLOSS_MODEL_ENV) or _DEFAULT_GLOSS_MODEL,
            referer=env.get(_REFERER_ENV) or None,
            app_title=env.get(_TITLE_ENV) or None,
        )

    def has_key(self) -> bool:
        return self.api_key is not None


def _headers(config: OpenRouterConfig) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    if config.referer is not None:
        headers["HTTP-Referer"] = config.referer
    if config.app_title is not None:
        headers["X-Title"] = config.app_title
    return headers


def _body(
    model: str,
    messages: list[ChatMessage],
    *,
    max_tokens: int,
    stream: bool,
) -> dict[str, object]:
    return {
        "model": model,
        "messages": [m.to_dict() for m in messages],
        "max_tokens": max_tokens,
        "stream": stream,
    }


def _content_from_payload(payload: dict[str, object]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OpenRouterError("OpenRouter returned no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise OpenRouterError("OpenRouter returned a malformed choice")
    message = first.get("message")
    if not isinstance(message, dict):
        raise OpenRouterError("OpenRouter returned a choice without a message")
    content = message.get("content")
    if not isinstance(content, str):
        raise OpenRouterError("OpenRouter returned a message without text content")
    return content


@dataclass
class OpenRouterClient:
    config: OpenRouterConfig
    transport: httpx.BaseTransport | None = field(default=None)

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.config.base_url,
            timeout=self.config.timeout_seconds,
            transport=self.transport,
        )

    def model_for(self, *, gloss: bool) -> str:
        return self.config.gloss_model if gloss else self.config.reasoning_model

    def complete(
        self,
        messages: list[ChatMessage],
        *,
        gloss: bool = False,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> str:
        if not self.config.has_key():
            raise MissingApiKeyError()
        model = self.model_for(gloss=gloss)
        body = _body(model, messages, max_tokens=max_tokens, stream=False)
        with self._client() as client:
            try:
                response = client.post(
                    "/chat/completions", json=body, headers=_headers(self.config)
                )
            except httpx.HTTPError as exc:
                raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc
            if response.status_code >= 400:
                raise OpenRouterError(
                    f"OpenRouter returned {response.status_code}",
                    status_code=response.status_code,
                )
            payload = response.json()
        if not isinstance(payload, dict):
            raise OpenRouterError("OpenRouter returned a non-object body")
        return _content_from_payload(payload)

    def stream(
        self,
        messages: list[ChatMessage],
        *,
        gloss: bool = False,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> Iterator[str]:
        if not self.config.has_key():
            raise MissingApiKeyError()
        model = self.model_for(gloss=gloss)
        body = _body(model, messages, max_tokens=max_tokens, stream=True)
        with self._client() as client:
            try:
                with client.stream(
                    "POST",
                    "/chat/completions",
                    json=body,
                    headers=_headers(self.config),
                ) as response:
                    if response.status_code >= 400:
                        raise OpenRouterError(
                            f"OpenRouter returned {response.status_code}",
                            status_code=response.status_code,
                        )
                    for line in response.iter_lines():
                        token = _token_from_sse_line(line)
                        if token is not None:
                            yield token
            except httpx.HTTPError as exc:
                raise OpenRouterError(f"OpenRouter stream failed: {exc}") from exc


def _token_from_sse_line(line: str) -> str | None:
    if not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if not data or data == "[DONE]":
        return None
    import json  # noqa: PLC0415

    try:
        payload = json.loads(data)
    except ValueError:
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    delta = choices[0].get("delta") if isinstance(choices[0], dict) else None
    if not isinstance(delta, dict):
        return None
    content = delta.get("content")
    return content if isinstance(content, str) and content else None
