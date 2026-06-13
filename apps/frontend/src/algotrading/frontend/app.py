"""FastAPI application factory: wires the JSON API routers over the real infra store.

``create_app`` takes an injectable :class:`~algotrading.frontend.context.AppContext`
(tests pass a tmp-store context; production resolves the repo root). Routers are imported
inside the factory so the module stays importable even while individual routers are in flux.

The BFF reads only ``packages/infra`` seams (down-layer): ``ParquetStore`` for the
persisted contract tables, the pure ``surfaces``/``risk`` engines, and
``orchestration.build_dashboard``. It never reaches into ``backend``. The routers —
``health``, ``surfaces``, ``risk``, ``run``, ``config``, the Tab-1 front-page
seams ``price-history``, ``constituents``, ``analytics``, and ``recorded-dates`` (WS 1I), and the
Tab-2 ``basket`` composer (WS 2A) — each call infra and serialize; no business logic lives
in them. The 1I routers read the real
``daily_bar`` / ``index_constituents`` / ``projected_option_analytics`` tables and the 1G run
ledger back through the read-only store (ADR 0034 §1).

App-lifetime state hangs off ``app.state`` (never module globals): the context (``ctx``)
and the pipeline job runner (``runner`` — its worker pool is shut down by the lifespan
handler). Routers reach all of it through the dependencies in
:mod:`algotrading.frontend.deps`. (The Saxo OAuth router + CSRF store were removed in
T-index-only-refactor along with the Saxo broker package.)

Malformed-request errors travel as :class:`~algotrading.frontend.deps.BadRequestError`
(carrying the exact labelled payload) or :class:`ContractValidationError` (a basket leg
violating its contract); the two exception handlers below serialize them as the same
labelled 400s the routers historically emitted inline — the wire contract is unchanged.

The earlier Codex ``market``/``orders`` paper-trading routers were dropped in C4: they
synthesized ~700 lines of fixture data, had no backend equivalent, and are superseded by
the store-backed surfaces/risk routers.
"""

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

# Dev (Vite) and prod origins both come from one env var so CORS is not hard-coded.
_DEFAULT_FRONTEND_ORIGIN = "http://localhost:5173"


def create_app(ctx: AppContext | None = None) -> FastAPI:
    """Build and wire the FastAPI application.

    ``ctx`` is injectable for tests; when omitted, ``AppContext.build()`` resolves the
    workspace root and wires the canonical ``data/`` store.
    """
    if ctx is None:
        ctx = AppContext.build()

    # One platform-wide logging config (M8); idempotent, so per-test app builds are fine.
    configure_logging()

    runner = PipelineRunner()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        """Release the runner's worker pool when the app shuts down."""
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
        """Serialize the exact labelled 400 payload a dependency or handler raised."""
        return JSONResponse(exc.payload, status_code=400)

    @app.exception_handler(ContractValidationError)
    async def emit_bad_basket(
        _request: Request, exc: ContractValidationError
    ) -> JSONResponse:
        """A request that builds an invalid contract (a malformed basket leg) is a 400."""
        return JSONResponse({"error": "bad_basket", "detail": str(exc)}, status_code=400)

    from .routers import analytics as analytics_router  # noqa: PLC0415
    from .routers import basket as basket_router  # noqa: PLC0415
    from .routers import config as config_router  # noqa: PLC0415
    from .routers import constituents as constituents_router  # noqa: PLC0415
    from .routers import coverage as coverage_router  # noqa: PLC0415
    from .routers import health as health_router  # noqa: PLC0415
    from .routers import indices as indices_router  # noqa: PLC0415
    from .routers import price_history as price_history_router  # noqa: PLC0415
    from .routers import recorded_dates as recorded_dates_router  # noqa: PLC0415
    from .routers import risk as risk_router  # noqa: PLC0415
    from .routers import run as run_router  # noqa: PLC0415
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

    @app.get("/healthz", tags=["ops"])
    def liveness() -> JSONResponse:
        """Liveness probe: 200 when the process is up (no infra reads)."""
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
