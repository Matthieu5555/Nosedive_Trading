from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from algotrading.infra.contracts import ContractValidationError
from algotrading.infra.observability import configure_logging
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .context import AppContext
from .deps import BadRequestError
from .runner import PipelineRunner

_DEFAULT_FRONTEND_ORIGIN = "http://localhost:5173"


def create_app(ctx: AppContext | None = None) -> FastAPI:
    if ctx is None:
        ctx = AppContext.build()

    configure_logging()

    runner = PipelineRunner()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        runner.shutdown()

    app = FastAPI(title="AlgoTrading Dashboard (BFF)", version="0.1.0", lifespan=lifespan)
    app.state.ctx = ctx
    app.state.runner = runner

    frontend_origin = os.getenv("FRONTEND_BASE_URL", _DEFAULT_FRONTEND_ORIGIN)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[frontend_origin],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    @app.exception_handler(BadRequestError)
    async def emit_labelled_400(_request: Request, exc: BadRequestError) -> JSONResponse:
        return JSONResponse(exc.payload, status_code=400)

    @app.exception_handler(ContractValidationError)
    async def emit_bad_basket(
        _request: Request, exc: ContractValidationError
    ) -> JSONResponse:
        return JSONResponse({"error": "bad_basket", "detail": str(exc)}, status_code=400)

    from .routers import analytics as analytics_router  # noqa: PLC0415
    from .routers import attribution as attribution_router  # noqa: PLC0415
    from .routers import backtest as backtest_router  # noqa: PLC0415
    from .routers import basket as basket_router  # noqa: PLC0415
    from .routers import booking as booking_router  # noqa: PLC0415
    from .routers import compose as compose_router  # noqa: PLC0415
    from .routers import config as config_router  # noqa: PLC0415
    from .routers import constituents as constituents_router  # noqa: PLC0415
    from .routers import coverage as coverage_router  # noqa: PLC0415
    from .routers import health as health_router  # noqa: PLC0415
    from .routers import indices as indices_router  # noqa: PLC0415
    from .routers import positions as positions_router  # noqa: PLC0415
    from .routers import price_history as price_history_router  # noqa: PLC0415
    from .routers import reconciliation as reconciliation_router  # noqa: PLC0415
    from .routers import recorded_dates as recorded_dates_router  # noqa: PLC0415
    from .routers import risk as risk_router  # noqa: PLC0415
    from .routers import run as run_router  # noqa: PLC0415
    from .routers import signals as signals_router  # noqa: PLC0415
    from .routers import surfaces as surfaces_router  # noqa: PLC0415
    from .routers import ticket as ticket_router  # noqa: PLC0415

    app.include_router(health_router.router)
    app.include_router(surfaces_router.router)
    app.include_router(risk_router.router)
    app.include_router(run_router.router)
    app.include_router(config_router.router)
    app.include_router(indices_router.router)
    app.include_router(price_history_router.router)
    app.include_router(constituents_router.router)
    app.include_router(analytics_router.router)
    app.include_router(recorded_dates_router.router)
    app.include_router(basket_router.router)
    app.include_router(coverage_router.router)
    app.include_router(ticket_router.router)
    app.include_router(booking_router.router)
    app.include_router(attribution_router.router)
    app.include_router(signals_router.router)
    app.include_router(positions_router.router)
    app.include_router(backtest_router.router)
    app.include_router(reconciliation_router.router)
    app.include_router(compose_router.router)

    @app.get("/healthz", tags=["ops"])
    def liveness() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
