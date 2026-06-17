from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class OrderSubmitError(Exception):
    pass


@runtime_checkable
class SupportsOrderPost(Protocol):

    def post(self, path: str, body: dict[str, Any] | None = None) -> Any: ...


@dataclass(frozen=True, slots=True)
class OrderAck:

    order_id: str
    status: str


class CpRestOrderSubmit:

    def __init__(self, transport: SupportsOrderPost, *, account_id: str) -> None:
        if not account_id.strip():
            raise OrderSubmitError("an account id is required")
        self._transport = transport
        self._account_id = account_id

    def submit(self, order: dict[str, Any]) -> OrderAck:
        path = f"/iserver/account/{self._account_id}/orders"
        response = self._transport.post(path, {"orders": [order]})
        if not isinstance(response, list) or not response:
            raise OrderSubmitError(f"unexpected order response: {response!r}")
        head = response[0]
        return OrderAck(
            order_id=str(head.get("order_id", head.get("id", ""))),
            status=str(head.get("order_status", head.get("status", ""))),
        )
