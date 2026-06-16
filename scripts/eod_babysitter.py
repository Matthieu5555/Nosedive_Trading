"""Thin shim over :mod:`algotrading.infra_ibkr.babysitter` — the importable, tested core.

Keeps the CP Gateway session warm and fires each enabled index's EOD close-capture at its own
session close (default), or heartbeats the session indefinitely (``--no-fire``). All the logic —
heartbeat self-heal, the planned-fire schedule, the per-index fire, and the fire-loop exit code —
lives in the package module so it sits in the gate and is unit-tested with an injected clock; this
shim only forwards ``argv`` to :func:`main`. Run detached so it outlives your shell:

    setsid bash -c 'uv run python scripts/eod_babysitter.py > /tmp/eod_babysitter.log 2>&1' &
    setsid bash -c \\
        'uv run python scripts/eod_babysitter.py --no-fire > /tmp/gateway_keepalive.log 2>&1' &
    tail -f /tmp/eod_babysitter.log

Requires an already-authenticated Gateway (run ``scripts/ibkr_gateway_login.py`` first).
"""

from __future__ import annotations

import sys

from algotrading.infra_ibkr.babysitter import main

if __name__ == "__main__":
    sys.exit(main())
