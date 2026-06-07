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

The 1C live-vs-empty selection lives here, in the one place that legitimately sees both the
runner (``algotrading.infra``) and the IBKR broker leaf (``algotrading.infra_ibkr``) — the
import-linter layering forbids the runner from importing the broker leaf, so the runner exposes a
``basket_source`` seam and this shim picks the source: a credentialed environment binds the live
``collect_live`` CP REST capture (a real basket from the gateway); a non-credentialed one falls
back to the runner's empty no-capture source (a clean exit-0 day). ``scripts/`` is outside the
root gate, so this cross-layer wiring is allowed exactly here and nowhere in the packages.

Usage:
    uv run python scripts/eod_run.py                       # all enabled indices, today
    uv run python scripts/eod_run.py --calendar XEUR       # the Eurex group (e.g. SX5E)
    uv run python scripts/eod_run.py --index SPX --trade-date 2026-06-05   # backfill one day
"""

from __future__ import annotations

from algotrading.infra.orchestration.eod_runner import RunnerDeps, build_default_deps, main
from algotrading.infra_ibkr.live_capture import live_basket_source


def _deps_factory() -> RunnerDeps:
    """Build the production deps with the live IBKR basket source when credentials are present.

    Selects the 1C close-capture source: ``live_basket_source()`` returns a credentialed
    ``collect_live`` CP REST source, or ``None`` when the environment carries no IBKR CP OAuth
    artifacts. ``build_default_deps(basket_source=...)`` threads whichever it gets into the stage
    wiring; ``None`` leaves the runner on its empty no-capture default (a clean exit-0 day).
    """
    return build_default_deps(basket_source=live_basket_source())


if __name__ == "__main__":
    raise SystemExit(main(deps_factory=_deps_factory))
