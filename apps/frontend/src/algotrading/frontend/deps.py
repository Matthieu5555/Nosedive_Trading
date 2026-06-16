from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import Depends, Request

from .context import AppContext


class BadRequestError(Exception):

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        super().__init__(str(payload))


def get_context(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.ctx
    return ctx


CtxDep = Annotated[AppContext, Depends(get_context)]


def _parse_trade_date(trade_date: str | None = None) -> date | None:
    if trade_date is None:
        return None
    try:
        return date.fromisoformat(trade_date)
    except ValueError:
        raise BadRequestError(
            {"error": "bad_trade_date", "trade_date": trade_date}
        ) from None


TradeDateDep = Annotated[date | None, Depends(_parse_trade_date)]


def _parse_as_of(as_of: str | None = None) -> date | None:
    if as_of is None:
        return None
    try:
        return date.fromisoformat(as_of)
    except ValueError:
        raise BadRequestError({"error": "bad_as_of", "as_of": as_of}) from None


AsOfDep = Annotated[date | None, Depends(_parse_as_of)]


@dataclass(frozen=True, slots=True)
class DateWindow:

    start: date | None
    end: date | None


def _parse_date_window(start: str | None = None, end: str | None = None) -> DateWindow:
    try:
        return DateWindow(
            start=date.fromisoformat(start) if start is not None else None,
            end=date.fromisoformat(end) if end is not None else None,
        )
    except ValueError:
        raise BadRequestError({"error": "bad_date", "start": start, "end": end}) from None


DateWindowDep = Annotated[DateWindow, Depends(_parse_date_window)]


async def parse_json_body(request: Request, *, error: str) -> object:
    try:
        return await request.json()
    except ValueError as exc:
        raise BadRequestError(
            {"error": error, "detail": "body is not valid JSON"}
        ) from exc
