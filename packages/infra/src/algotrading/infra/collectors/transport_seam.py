"""The REST transport seam collectors consume — defined once, satisfied by every broker leaf.

Every REST-polling collector (IBKR discovery/index/history/close-capture, the Saxo underlying
probe) needs the same thing from its transport: ``get(path, params) -> decoded JSON``; the
session layer additionally needs ``post``. The protocols used to be copy-pasted seven times
across the leaves (the 2026-06 maintainability audit, M40); this module is the one canonical
definition. It lives with the collectors because they are the consumers that define the seam —
concrete transports (``CpRestTransport``, ``SaxoTransport``) satisfy it structurally, tests
satisfy it with a fake.

``runtime_checkable`` so wiring code can assert an injected transport structurally
(``isinstance(transport, SupportsRestGet)``) instead of duck-checking ``getattr``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SupportsRestGet(Protocol):
    """The read-only REST transport seam: anything with the transport's ``get``."""

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


@runtime_checkable
class SupportsRest(SupportsRestGet, Protocol):
    """The read/write REST transport seam: ``get`` + ``post`` (the session layer needs both)."""

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any: ...
