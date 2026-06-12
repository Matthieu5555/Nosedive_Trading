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

# Web app verification (AGENTS.md frontend gate): eslint + vitest in apps/frontend/web.
web-test:
    cd apps/frontend/web && npm run lint && npm test
