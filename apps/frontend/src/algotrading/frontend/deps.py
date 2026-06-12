"""Request-scoped dependencies shared by every router (the BFF's FastAPI DI seams).

One home for the three things each router used to re-implement privately:

* ``CtxDep`` — the wired :class:`~algotrading.frontend.context.AppContext` off
  ``app.state`` (replaces the per-router ``_context(request)`` copies);
* the ISO-date query dependencies (``TradeDateDep`` / ``AsOfDep`` / ``DateWindowDep``),
  which raise :class:`BadRequestError` carrying the exact labelled 400 payload the
  routers previously built inline — the app-level handler in ``create_app`` emits the
  payload unchanged, so the wire contract stays byte-identical;
* :func:`parse_json_body` — the shared "JSON body or labelled 400" gate for the POST
  routes that keep their own error label (``bad_basket`` / ``bad_batch``) instead of
  FastAPI's default 422.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Annotated

from fastapi import Depends, Request

from .context import AppContext


class BadRequestError(Exception):
    """A malformed request, carrying the exact labelled 400 payload to emit.

    Raised by dependencies and handlers instead of building a ``JSONResponse`` inline;
    the app-level exception handler (``create_app``) serializes ``payload`` with HTTP
    400, so every route keeps its historical error shape without restating it.
    """

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        super().__init__(str(payload))


def get_context(request: Request) -> AppContext:
    """The app-lifetime context wired by ``create_app`` (tests inject a tmp-store one)."""
    ctx: AppContext = request.app.state.ctx
    return ctx


CtxDep = Annotated[AppContext, Depends(get_context)]


def _parse_trade_date(trade_date: str | None = None) -> date | None:
    """``?trade_date=`` as a date; a malformed value is a labelled ``bad_trade_date`` 400."""
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
    """``?as_of=`` as a date; a malformed value is a labelled ``bad_as_of`` 400."""
    if as_of is None:
        return None
    try:
        return date.fromisoformat(as_of)
    except ValueError:
        raise BadRequestError({"error": "bad_as_of", "as_of": as_of}) from None


AsOfDep = Annotated[date | None, Depends(_parse_as_of)]


@dataclass(frozen=True, slots=True)
class DateWindow:
    """An optional inclusive ``[start, end]`` query window, parsed from ISO dates."""

    start: date | None
    end: date | None


def _parse_date_window(start: str | None = None, end: str | None = None) -> DateWindow:
    """``?start=&end=`` as a window; a malformed bound is a labelled ``bad_date`` 400."""
    try:
        return DateWindow(
            start=date.fromisoformat(start) if start is not None else None,
            end=date.fromisoformat(end) if end is not None else None,
        )
    except ValueError:
        raise BadRequestError({"error": "bad_date", "start": start, "end": end}) from None


DateWindowDep = Annotated[DateWindow, Depends(_parse_date_window)]


async def parse_json_body(request: Request, *, error: str) -> object:
    """The request's JSON body, or a labelled 400 under the route's error label."""
    try:
        return await request.json()
    except ValueError as exc:
        raise BadRequestError(
            {"error": error, "detail": "body is not valid JSON"}
        ) from exc
