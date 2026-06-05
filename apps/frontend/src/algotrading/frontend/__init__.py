"""algotrading.frontend — FastAPI BFF + React web app for the AlgoTrading platform.

Python BFF
----------
The backend-for-frontend is a FastAPI application (``app.py`` / ``create_app()``) that
exposes eight JSON API routers over the real ``packages/infra`` store:

  ``/api/health``    — operator dashboard status (C3 seam, defensive)
  ``/api/surfaces``  — fitted SVI surfaces from the store
  ``/api/risk``      — portfolio risk aggregates + scenario PnL
  ``/api/run``       — provider listing, pipeline launch, job polling
  ``/api/config``    — platform config file listing + read
  ``/api/oauth``     — Saxo authorization-code CSRF flow
  ``/api/market``    — Codex paper market data (fixture-backed)
  ``/api/orders``    — Codex paper orders (fixture-backed)

Application context (``AppContext``) is built once at startup and injected via
``app.state.ctx``; tests pass a tmp-store context via ``create_app(ctx=...)``.

Web app
-------
The companion Vite/React SPA lives in ``web/`` and is served separately by Vite in
development. All API calls go to the FastAPI BFF via the ``/api`` prefix.
"""
