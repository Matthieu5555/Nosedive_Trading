from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SupportsRestGet(Protocol):

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


@runtime_checkable
class SupportsRest(SupportsRestGet, Protocol):

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any: ...
