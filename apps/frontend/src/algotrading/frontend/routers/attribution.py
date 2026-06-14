"""Attribution router: surface the by-Greek P&L decomposition on the front (§7 #2).

Reads the persisted ``scenario_attributions`` contract back from the store and serializes one
record into the waterfall payload the web panel renders: the per-Greek dollar contributions, the
residual against the full reprice (the honesty meter), and the tolerance verdict.

**The BFF re-decomposes nothing.** It serializes ``infra/risk/attribution.py``'s output verbatim
(``serializers.scenario_attribution_to_dict``); it never sums Greeks, reprices, or invents a
term. A number not already on the ``ScenarioAttribution`` seam does not appear here.

The endpoint selects one record by ``(level, portfolio_id[, contract_key])`` on a trade date —
the **book** aggregate by default (``level=book``, the book sentinel in ``contract_key``), or one
position's drill (``level=position`` + the leg's ``contract_key``), the §5.8 drill target. A
malformed ``trade_date`` yields a labelled 400; no attribution for the ``(book/portfolio, date)``
yields a labelled-empty body with HTTP 200, never a 500. Serving is read-only (ADR 0034 §1).
"""

from __future__ import annotations

from datetime import date

from algotrading.infra.contracts import ScenarioAttribution
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import AppContext
from ..serializers import (
    ATTRIBUTION_RESIDUAL_UNIT,
    ATTRIBUTION_TERM_UNIT,
    scenario_attribution_to_dict,
)

router = APIRouter(prefix="/api/attribution", tags=["attribution"])

# The book aggregate rides the book sentinel in ``contract_key`` (mirrors
# infra.risk.attribution.BOOK_CONTRACT_KEY / LEVEL_BOOK), so a book and a per-line record never
# collide. The default view is the book; the per-position drill passes level=position + a key.
_LEVEL_BOOK = "book"
_BOOK_CONTRACT_KEY = "__book__"


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


def _empty_body(
    *,
    trade_date: str | None,
    portfolio_id: str | None,
    level: str,
    contract_key: str | None,
) -> dict[str, object]:
    """A labelled-empty attribution body: no record for this selection, never a 500.

    Carries the empty ``terms``/``residual`` (still unit-labelled, so the panel renders an honest
    empty waterfall rather than a blank) and ``found=False`` so the front shows its empty state.
    """
    return {
        "trade_date": trade_date,
        "portfolio_id": portfolio_id,
        "level": level,
        "contract_key": contract_key,
        "found": False,
        "terms": [],
        "residual": {"dollars": None, "unit": ATTRIBUTION_RESIDUAL_UNIT},
        "residual_unit": ATTRIBUTION_RESIDUAL_UNIT,
        "term_unit": ATTRIBUTION_TERM_UNIT,
        "verdict": None,
    }


@router.get("")
def get_attribution(
    request: Request,
    trade_date: str | None = None,
    portfolio_id: str | None = None,
    level: str = _LEVEL_BOOK,
    contract_key: str | None = None,
) -> JSONResponse:
    """Return one by-Greek attribution record's waterfall payload.

    ``level`` selects the book aggregate (default) or one position (``position`` + ``contract_key``,
    the §5.8 drill target). With ``trade_date=None`` the store returns every partition and the
    record is selected in Python, so an unknown ``(portfolio, date)`` resolves to a labelled-empty
    body, not a 500. A malformed ``trade_date`` is a labelled 400.
    """
    ctx = _context(request)
    try:
        resolved_date = _parse_date(trade_date)
    except ValueError:
        return JSONResponse({"error": "bad_trade_date", "trade_date": trade_date}, status_code=400)

    target_key = contract_key if level != _LEVEL_BOOK else _BOOK_CONTRACT_KEY
    rows: list[ScenarioAttribution] = ctx.store.read(
        "scenario_attributions", trade_date=resolved_date
    )
    matches = [
        row
        for row in rows
        if row.level == level
        and (portfolio_id is None or row.portfolio_id == portfolio_id)
        and (target_key is None or row.contract_key == target_key)
    ]
    if not matches:
        return JSONResponse(
            _empty_body(
                trade_date=trade_date,
                portfolio_id=portfolio_id,
                level=level,
                contract_key=contract_key,
            )
        )

    # One record per (level, portfolio, contract_key) on a trade date; if a stale mix is present
    # (e.g. two scenario versions before the cron rewrites the partition), surface the latest
    # valuation deterministically rather than guessing.
    record = max(matches, key=lambda row: row.valuation_ts)
    body = scenario_attribution_to_dict(record)
    body["found"] = True
    return JSONResponse(body)
