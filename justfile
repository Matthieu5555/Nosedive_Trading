# Task runner for the AlgoTrading monorepo (M2 / REP12). `just --list` shows
# everything. No `just` on the box? `uv tool run --from rust-just just <recipe>`
# works without installing anything into the project.
#
# Recipes encode existing entrypoints verbatim — the gate from AGENTS.md and the
# operator scripts under scripts/ — so the commands live in one greppable place
# instead of operator memory. CI (.github/workflows/gate.yml) runs the same gate.
#
# `just --list` shows the comment line directly above each recipe; longer notes
# sit above that line.

# List available recipes.
default:
    @just --list

# The full quality gate (AGENTS.md "Verify before you declare done").
gate:
    uv run ruff check .
    uv run mypy .
    uv run lint-imports
    uv run pytest -q

# scripts/smoke_e2e.py: replays the committed synthetic chain into a temp
# store, probes the BFF, runs the web build+tests (SKIPs cleanly if npm is
# absent), and asserts byte-identical replay.
# Offline end-to-end smoke walk. Exit: 0 healthy, 1 spine broken, 2 degraded (a SKIP/soft failure).
smoke *args='':
    uv run python scripts/smoke_e2e.py {{ args }}

# The same entrypoint the systemd timer runs (scripts/eod_run.py). Extra flags
# (e.g. `just eod '' --index SPX --trade-date 2026-06-05`) pass straight through.
# EOD close-capture fire. `just eod` = all enabled indices, today; `just eod XEUR` = one calendar group.
eod calendar='' *args='':
    uv run python scripts/eod_run.py {{ if calendar == '' { '' } else { '--calendar ' + calendar } }} {{ args }}

# Flags pass through: --period 5y --index SX5E --as-of 2026-06-01
# --no-constituents --refresh-tail --max-windows 1.
# IBKR daily OHLC backfill (scripts/ohlc_backfill.py). Exits 0 cleanly without credentials.
backfill *args='':
    uv run python scripts/ohlc_backfill.py {{ args }}

# `just login` = live, `just login paper` = paper; extra flags pass through
# (--code N, --wait-code-file PATH, --code-timeout SECS).
# Headless IBKR CP Gateway login with SMS 2FA (scripts/ibkr_gateway_login.py).
login mode='live' *args='':
    uv run python scripts/ibkr_gateway_login.py --mode {{ mode }} {{ args }}

# Web app verification (AGENTS.md frontend gate): prettier + eslint (boundaries) + tsc + vitest.
web-test:
    cd apps/frontend/web && npm run format:check && npm run lint && npm run typecheck && npm test

# Real-browser e2e (Playwright): navigation + layout-collision / overflow checks.
# Needs the Chromium binary once: `cd apps/frontend/web && npx playwright install chromium`.
# Mirrors the `web-e2e` CI job; network is mocked so it never touches a live BFF.
web-e2e:
    cd apps/frontend/web && npm run e2e

# Frontend↔BFF contract drift guard (needs both uv and node). Regenerates the exported
# OpenAPI schema and the TS types from it, then fails if either differs from the committed
# copy — so a backend contract change that was not regenerated breaks the build. Mirrors the
# `web-contract` CI job. Run `uv run python scripts/export_openapi.py` + `npm run gen:api`
# and commit both artifacts to clear it.
web-contract:
    uv run python scripts/export_openapi.py
    cd apps/frontend/web && npm run gen:api
    git diff --exit-code apps/frontend/web/openapi.json apps/frontend/web/src/api/schema.d.ts
