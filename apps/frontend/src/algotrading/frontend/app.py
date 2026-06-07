"""FastAPI application factory: wires the JSON API routers over the real infra store.

``create_app`` takes an injectable :class:`~algotrading.frontend.context.AppContext`
(tests pass a tmp-store context; production resolves the repo root). Routers are imported
inside the factory so the module stays importable even while individual routers are in flux.

The BFF reads only ``packages/infra`` seams (down-layer): ``ParquetStore`` for the
persisted contract tables, the pure ``surfaces``/``risk`` engines, and
``orchestration.build_dashboard``. It never reaches into ``backend``. The routers —
``health``, ``surfaces``, ``risk``, ``run``, ``config``, ``oauth``, the Tab-1 front-page
seams ``price-history``, ``constituents``, ``analytics``, and ``recorded-dates`` (WS 1I), and the
Tab-2 ``basket`` composer (WS 2A) — each call infra and serialize; no business logic lives
in them. The 1I routers read the real
``daily_bar`` / ``index_constituents`` / ``projected_option_analytics`` tables and the 1G run
ledger back through the read-only store (ADR 0034 §1).

The earlier Codex ``market``/``orders`` paper-trading routers were dropped in C4: they
synthesized ~700 lines of fixture data, had no backend equivalent, and are superseded by
the store-backed surfaces/risk routers.
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

    frontend_origin = os.getenv("FRONTEND_BASE_URL", _DEFAULT_FRONTEND_ORIGIN)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[frontend_origin],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )

    from .routers import analytics as analytics_router  # noqa: PLC0415
    from .routers import basket as basket_router  # noqa: PLC0415
    from .routers import config as config_router  # noqa: PLC0415
    from .routers import constituents as constituents_router  # noqa: PLC0415
    from .routers import health as health_router  # noqa: PLC0415
    from .routers import oauth as oauth_router  # noqa: PLC0415
    from .routers import price_history as price_history_router  # noqa: PLC0415
    from .routers import recorded_dates as recorded_dates_router  # noqa: PLC0415
    from .routers import risk as risk_router  # noqa: PLC0415
    from .routers import run as run_router  # noqa: PLC0415
    from .routers import surfaces as surfaces_router  # noqa: PLC0415

    app.include_router(health_router.router)
    app.include_router(surfaces_router.router)
    app.include_router(risk_router.router)
    app.include_router(run_router.router)
    app.include_router(config_router.router)
    app.include_router(oauth_router.router)
    app.include_router(price_history_router.router)
    app.include_router(constituents_router.router)
    app.include_router(analytics_router.router)
    app.include_router(recorded_dates_router.router)
    app.include_router(basket_router.router)

    @app.get("/healthz", tags=["ops"])
    def liveness() -> JSONResponse:
        """Liveness probe: 200 when the process is up (no infra reads)."""
        return JSONResponse({"status": "ok"})

    return app


app = create_app()
