"""FastAPI application factory: wires all routers over the real infra store.

``create_app`` takes an injectable :class:`~algotrading.frontend.context.AppContext`
(tests pass a tmp-store context; production resolves the repo root). Routers are imported
inside the factory so the module stays importable even while individual routers are in flux.

Router inventory
----------------
Real infra (packages/infra-backed):
  health, surfaces, risk, run, config, oauth

Codex extras (paper/fixture-backed, kept forward):
  market, orders

The ``risk`` router is the real backend version (``risk_aggregates`` + ``scenario_results``
partitions). The Codex scenario sub-router is gone — the web ``RiskScenarios`` page uses
the ``market`` fixture path instead.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .context import AppContext

# Dev (Vite) and prod origins both come from one env var so CORS is not hard-coded.
_DEFAULT_FRONTEND_ORIGIN = "http://localhost:5173"


def create_app(ctx: AppContext | None = None) -> FastAPI:
    """Build and wire the FastAPI application.

    ``ctx`` is injectable for tests; when omitted, ``AppContext.build()`` resolves the
    workspace root and wires the canonical ``data/`` store.
    """
    if ctx is None:
        ctx = AppContext.build()

    app = FastAPI(title="AlgoTrading Dashboard (BFF)", version="0.1.0")
    app.state.ctx = ctx
    # Keep app.state.store for the Codex market router which reads directly from the store.
    app.state.store = ctx.store

    frontend_origin = os.getenv("FRONTEND_BASE_URL", _DEFAULT_FRONTEND_ORIGIN)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[frontend_origin],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    # Real infra routers.
    from .routers import config as config_router  # noqa: PLC0415
    from .routers import health as health_router  # noqa: PLC0415
    from .routers import oauth as oauth_router  # noqa: PLC0415
    from .routers import risk as risk_router  # noqa: PLC0415
    from .routers import run as run_router  # noqa: PLC0415
    from .routers import surfaces as surfaces_router  # noqa: PLC0415

    app.include_router(health_router.router)
    app.include_router(surfaces_router.router)
    app.include_router(risk_router.router)
    app.include_router(run_router.router)
    app.include_router(config_router.router)
    app.include_router(oauth_router.router)

    # Codex paper/fixture routers (market data + paper orders) kept forward.
    from .routers import market  # noqa: PLC0415
    from .routers import orders  # noqa: PLC0415

    app.include_router(market.router)
    app.include_router(orders.router)

    @app.get("/healthz", tags=["ops"])
    def liveness() -> JSONResponse:
        """Liveness probe: 200 when the process is up (no infra reads)."""
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
