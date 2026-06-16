from __future__ import annotations

from collections.abc import Callable
from typing import Any

_UNSET: Any = object()


class FakeCpTransport:

    def __init__(
        self,
        *,
        get_responder: Callable[[str, dict[str, Any]], Any] | None = None,
        get_routes: dict[str, Any] | None = None,
        get_queue: list[Any] | None = None,
        get_response: Any = _UNSET,
        get_errors: list[Exception] | None = None,
        post_responder: Callable[[str, dict[str, Any]], Any] | None = None,
        post_routes: dict[str, Any] | None = None,
        post_queue: list[Any] | None = None,
        post_response: Any = _UNSET,
    ) -> None:
        self._get_responder = get_responder
        self._get_routes = get_routes
        self._get_queue = list(get_queue or [])
        self._get_response = get_response
        self._get_errors = list(get_errors or [])
        self._post_responder = post_responder
        self._post_routes = post_routes
        self._post_queue = list(post_queue or [])
        self._post_response = post_response
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def get_paths(self) -> list[str]:
        return [path for path, _ in self.get_calls]

    @property
    def post_paths(self) -> list[str]:
        return [path for path, _ in self.post_calls]

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        payload = dict(params or {})
        self.get_calls.append((path, payload))
        if self._get_errors:
            raise self._get_errors.pop(0)
        return self._resolve(
            "GET", path, payload, self._get_responder, self._get_routes, self._get_queue,
            self._get_response,
        )

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        payload = dict(body or {})
        self.post_calls.append((path, payload))
        return self._resolve(
            "POST", path, payload, self._post_responder, self._post_routes, self._post_queue,
            self._post_response,
        )

    @staticmethod
    def _resolve(
        verb: str,
        path: str,
        payload: dict[str, Any],
        responder: Callable[[str, dict[str, Any]], Any] | None,
        routes: dict[str, Any] | None,
        queue: list[Any],
        response: Any,
    ) -> Any:
        if responder is not None:
            return responder(path, payload)
        if routes is not None:
            return routes[path]
        if queue:
            return queue.pop(0)
        if response is not _UNSET:
            return response
        raise AssertionError(f"FakeCpTransport has no canned response for {verb} {path}")
