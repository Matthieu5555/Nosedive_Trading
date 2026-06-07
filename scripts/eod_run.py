"""EOD daily close-capture runner — the one-shot the systemd timer fires (WS 1G).

A thin shim over :func:`algotrading.infra.orchestration.eod_runner.main`: the runner logic
lives in the workspace package (importable, in the root gate, mypy/lint-imports-checked and
unit-tested in ``packages/infra/tests/test_eod_run.py``); this file is only the executable
entrypoint the ``eod-capture.service`` ExecStart invokes under ``uv``. The timer is the
scheduler (ADR 0032) — no scheduling lives here.

The runner resolves the trade date (default = the clock's current market day; ``--trade-date``
for a catch-up/backfill fire, a future date rejected — no look-ahead), scopes the fire to a
calendar group (``--calendar XEUR`` / ``--index SX5E``; default = all enabled), reads the 1J
index registry's enabled set, skips a non-session cleanly, captures each index at its own
``session_close``, binds one ``correlation_id`` for the fire, runs ``run_end_of_day``, and
freezes a per-run manifest. It exits non-zero on any stage failure so ``Restart=on-failure``
and ``OnFailure=`` engage.

Usage:
    uv run python scripts/eod_run.py                       # all enabled indices, today
    uv run python scripts/eod_run.py --calendar XEUR       # the Eurex group (e.g. SX5E)
    uv run python scripts/eod_run.py --index SPX --trade-date 2026-06-05   # backfill one day
"""

from __future__ import annotations

from algotrading.infra.orchestration.eod_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
