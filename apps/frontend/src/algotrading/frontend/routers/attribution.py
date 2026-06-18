from __future__ import annotations

from datetime import date

from algotrading.infra.contracts import ScenarioAttribution
from algotrading.infra.risk.config import AttributionConfig
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import AppContext
from ..realized_attribution import (
    RealizedAttributionInputError,
    attribute_day_steps,
    september_straddle_spec,
)
from ..serializers import (
    ATTRIBUTION_RESIDUAL_UNIT,
    ATTRIBUTION_TERM_UNIT,
    realized_day_step_to_dict,
    scenario_attribution_to_dict,
)

router = APIRouter(prefix="/api/attribution", tags=["attribution"])

_LEVEL_BOOK = "book"
_BOOK_CONTRACT_KEY = "__book__"
_REALIZED_ATTRIBUTION_VERSION = "realized-bff-v1"


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


_IV_POINTS_TABLE = "iv_points"
_DEMO_UNDERLYING = "SX5E"
_DEMO_EXPIRY = "2026-09-18"


def _banked_dates(
    ctx: AppContext, *, underlying: str, start: date, end: date
) -> list[date]:
    return sorted(
        part_date
        for part_date, part_underlying in ctx.store.list_partitions(_IV_POINTS_TABLE)
        if part_underlying == underlying and start <= part_date <= end
    )


@router.get("/realized")
def get_realized_attribution(
    request: Request,
    start_date: str | None = None,
    end_date: str | None = None,
    underlying: str = _DEMO_UNDERLYING,
    expiry: str = _DEMO_EXPIRY,
) -> JSONResponse:
    """Realized day-over-day Greek P&L explain for a fixed-expiry option book.

    Defaults to the demo showpiece: a long ATM straddle on the September SX5E expiry, swept
    across every consecutive banked close in [start_date, end_date] (the full banked window
    if unbounded). Each step carries the seven Taylor terms + full_reprice + residual +
    verdict, ready for a waterfall. The book is anchored to a *fixed* calendar expiry whose
    maturity rolls down with the actual days elapsed, so theta is real and the residual is
    small and honest (see realized_attribution module).
    """
    ctx = _context(request)
    try:
        parsed_start = _parse_date(start_date)
        parsed_end = _parse_date(end_date)
    except ValueError as exc:
        return JSONResponse(
            {"error": "bad_trade_date", "detail": str(exc)}, status_code=400
        )
    try:
        target_expiry = date.fromisoformat(expiry)
    except ValueError:
        return JSONResponse({"error": "bad_expiry", "expiry": expiry}, status_code=400)
    if parsed_start is not None and parsed_end is not None and parsed_end < parsed_start:
        return JSONResponse(
            {"error": "bad_window", "detail": "end_date precedes start_date"},
            status_code=400,
        )

    lo = parsed_start if parsed_start is not None else date.min
    hi = parsed_end if parsed_end is not None else date.max
    dates = _banked_dates(ctx, underlying=underlying, start=lo, end=hi)
    if len(dates) < 2:
        return JSONResponse(
            {
                "found": False,
                "underlying": underlying,
                "expiry": expiry,
                "portfolio_id": None,
                "dates": [d.isoformat() for d in dates],
                "steps": [],
                "detail": "need at least two banked dates to form a day-step",
            }
        )

    spec = september_straddle_spec(underlying=underlying, expiry=target_expiry)
    config = AttributionConfig(version=_REALIZED_ATTRIBUTION_VERSION)
    try:
        steps = attribute_day_steps(ctx.store, spec, dates, config)
    except RealizedAttributionInputError as exc:
        return JSONResponse(
            {
                "found": False,
                "underlying": underlying,
                "expiry": expiry,
                "error": exc.code,
                "detail": exc.detail,
                "dates": [d.isoformat() for d in dates],
                "steps": [],
            }
        )

    resolved_legs = steps[0].attribution.lines
    return JSONResponse(
        {
            "found": True,
            "underlying": underlying,
            "expiry": expiry,
            "portfolio_id": spec.portfolio_id,
            "term_unit": ATTRIBUTION_TERM_UNIT,
            "residual_unit": ATTRIBUTION_RESIDUAL_UNIT,
            "contracts": [line.contract_key for line in resolved_legs],
            "dates": [d.isoformat() for d in dates],
            "steps": [realized_day_step_to_dict(step) for step in steps],
        }
    )
