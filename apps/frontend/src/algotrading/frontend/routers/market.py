"""Market dashboard routes: store-backed first, fixture fallback."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..data import UnknownUnderlyingError, get_market_dashboard, json_payload, list_underlyings
from ..store_serving import market_dashboard_from_store, store_underlyings

router = APIRouter(prefix="/api", tags=["market"])


@router.get("/underlyings")
async def underlyings(request: Request) -> JSONResponse:
    """Store underlyings first (real pipeline output), then the fixture set."""

    from_store = store_underlyings(request.app.state.store)
    store_symbols = {choice.symbol for choice in from_store}
    fixtures = [choice for choice in list_underlyings() if choice.symbol not in store_symbols]
    return JSONResponse(json_payload({"underlyings": from_store + fixtures}))


@router.get("/market")
async def market_dashboard(request: Request, underlying: str = "SPX") -> JSONResponse:
    """Return snapshots, option quotes, greeks, and vol surface for one underlying."""

    dashboard = market_dashboard_from_store(request.app.state.store, underlying)
    if dashboard is None:
        try:
            dashboard = get_market_dashboard(underlying)
        except UnknownUnderlyingError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    return JSONResponse(json_payload(dashboard))
