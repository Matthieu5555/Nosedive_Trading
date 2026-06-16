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

    record = max(matches, key=lambda row: row.valuation_ts)
    body = scenario_attribution_to_dict(record)
    body["found"] = True
    return JSONResponse(body)
