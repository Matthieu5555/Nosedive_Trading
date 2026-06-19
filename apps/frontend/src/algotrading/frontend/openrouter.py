from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import httpx

if TYPE_CHECKING:
    from .assistant_structured import GroundedAnswer

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

_DEFAULT_REASONING_MODEL = "anthropic/claude-opus-4-8"
_DEFAULT_GLOSS_MODEL = "anthropic/claude-haiku-4-5"
_DEFAULT_TIMEOUT_SECONDS = 60.0
_DEFAULT_MAX_TOKENS = 1024

# The grounded structured path runs against a cheap, flaky flash model. Keep answers short (a
# couple of sentences never need more) so a fast empty response (qwen's usual failure) still
# leaves room for a retry. The web client no longer imposes a cutoff, so each attempt gets the
# full default transport budget rather than a tight grounded-specific cap that raced the model's
# slowest answers (which land near the old 30s mark).
_GROUNDED_MAX_TOKENS = 350
_GROUNDED_TIMEOUT_SECONDS = _DEFAULT_TIMEOUT_SECONDS
_GROUNDED_MAX_RETRIES = 2

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

    def complete_grounded(
        self,
        messages: list[ChatMessage],
        *,
        allowed_ids: set[str],
        max_retries: int = _GROUNDED_MAX_RETRIES,
        max_tokens: int = _GROUNDED_MAX_TOKENS,
    ) -> GroundedAnswer:
        """Force a grounded, schema-validated answer out of the reasoning model.

        Wraps the OpenRouter chat endpoint with `instructor`, so the model must return a
        `GroundedAnswer` whose numbers are all ``{fact_id}`` placeholders. `allowed_ids` is
        threaded into the pydantic validator through instructor's validation context, so an
        out-of-vocabulary placeholder or a bare digit is fed back to the model as an error
        and retried (up to `max_retries`) rather than silently failing the turn.

        instructor + the OpenAI SDK live behind this one method on purpose: the rest of the
        BFF (and every unit test) talks to the plain `complete`/`stream` seam, so the network
        client is never imported on the hot import path or exercised in tests.

        Returns a `GroundedAnswer`; when the model cannot produce a valid grounded answer
        within the retry budget, returns one with ``answerable=False`` so the caller degrades
        to the honest-gap line instead of surfacing a hard error. Raises `OpenRouterError`
        for transport / API failures (missing key, network, 4xx/5xx).
        """
        if not self.config.has_key():
            raise MissingApiKeyError()

        import instructor  # noqa: PLC0415  (kept off the hot import path)
        from instructor.exceptions import InstructorRetryException  # noqa: PLC0415
        from openai import APIStatusError, OpenAI, OpenAIError  # noqa: PLC0415

        from .assistant_structured import GroundedAnswer as _GroundedAnswer  # noqa: PLC0415

        client = instructor.from_openai(
            OpenAI(
                base_url=self.config.base_url,
                api_key=self.config.api_key,
                timeout=min(self.config.timeout_seconds, _GROUNDED_TIMEOUT_SECONDS),
                max_retries=0,  # instructor owns the retry loop; don't let the SDK double it
            ),
            mode=instructor.Mode.JSON,
        )
        try:
            return client.chat.completions.create(
                model=self.config.reasoning_model,
                # to_dict() yields valid OpenAI message dicts; cast past the SDK's typed union.
                messages=cast("Any", [m.to_dict() for m in messages]),
                response_model=_GroundedAnswer,
                context={"allowed_ids": set(allowed_ids)},
                max_retries=max_retries,
                max_tokens=max_tokens,
            )
        except InstructorRetryException:
            # The model could not produce a grounded answer within the retry budget. Degrade
            # to the honest gap rather than a 502, exactly as a self-declared gap would.
            return _GroundedAnswer(answerable=False, answer="", facts_used=[])
        except APIStatusError as exc:
            raise OpenRouterError(
                f"OpenRouter returned {exc.status_code}", status_code=exc.status_code
            ) from exc
        except OpenAIError as exc:
            raise OpenRouterError(f"OpenRouter request failed: {exc}") from exc

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
