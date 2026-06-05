# M8 — Frontend (API spine + web app)

- **Branch:** `feat/merge-frontend`
- **Owns:** `apps/frontend/**` (FastAPI backend-for-frontend + the React/Vite/TS web app).
- **Depends on:** the infra layer being importable — practically M2 (surfaces), M3 (risk), M7 (run/replay). Can scaffold against the frozen API contracts early; nothing imports the frontend, so it is structurally orthogonal.
- **Blocks:** nothing.

## Objective

Bring Vincent's frontend into the merged repo as the top layer. Ours is an empty `frontend/` stub, so this is **adopt wholesale, rewire to the merged infra**. The frontend is the only consumer at the top of the layering — it may import down, nothing imports it.

## What to merge

- **Adopt from Vincent (`apps/frontend/`):**
  - **API spine** (FastAPI): `src/algotrading/frontend/{app,context,providers,runner,sample_flow,serializers,oauth_state}.py` and `routers/{config,health,oauth,risk,run,surfaces}.py`. Rewire each router to call the merged infra — surfaces from M2, risk from M3, run/replay through M7's actor-driven jobs, oauth against M5's Saxo flow.
  - **Web app** (React/Vite/TS): `web/src/{App.tsx,main.tsx,layouts,hooks,components}` and `pages/{Home,Health,Config,Run,Risk,Surfaces,NotFound}`. Keep the page set; point `useFetch` at the merged API.
- Respect the layering: the frontend imports `execution`/`strategy`/`infra`/`core` downward only; import-linter (M0) enforces it. The BFF is the only place infra meets HTTP.

## Frozen seam

The HTTP API contract (the router request/response schemas + serializers) is the seam between the web app and infra. Freeze it so the web app and the routers can be built in parallel. Surfaces/risk/run responses carry provenance through to the UI where it aids the operator.

## Test surface

Read [TESTING.md] first. Specific to M8:
- API: adopt Vincent's `tests/test_{health,risk,run,surfaces,oauth,router_oauth,runner,sample_flow,providers,liveness}*.py`, rewired to the merged infra; each router returns well-formed responses against fixture infra and surfaces errors as typed payloads, not 500s.
- Web (per `write-tests` UI guidance): user-facing assertions on the page components — a Surfaces page renders a fitted surface from a fixture response; a Risk page renders aggregates; error and loading states are asserted, not just the happy path.
- Liveness/health endpoint reflects real infra health (wired to M7's observability), not a hardcoded OK.

## Done criteria

The FastAPI BFF + React app run against the merged infra, the six routers serve real surfaces/risk/run/config/health/oauth data, layering enforced by import-linter, API + component tests green, gate green. `uv run`/`npm run` start paths documented (M9 surfaces them in the README).

## Gotchas

The frontend is a read-only-ish consumer at the top — it must not reach sideways into another app or get imported by infra. Don't let business logic migrate into routers; they call infra and serialize. Keep secrets (Saxo oauth) server-side in the BFF, never shipped to the browser. The web build (`npm`) is a separate gate from the Python gate — wire both.
