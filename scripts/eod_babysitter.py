"""Keep the CP Gateway session warm and fire each index's EOD capture at its own close.

In production the systemd timer (ADR 0032) fires `eod_run.py` at each index's session close.
On a box with no timer — e.g. after a manual headless login (see
`documentation/connectivity/ibkr-gateway-headless-login.md`) — this stands in: it tickles the
Client Portal Gateway every 90s so the cookie session does not idle out, and fires the
close-capture for each *enabled* index just after that index's own `session_close` (derived from
its exchange calendar, so it is correct on half-days and across DST, any day — nothing hardcoded).

It writes to the canonical store, so this is the real harvest. Launch it detached so it outlives
your shell, and watch the log:

    setsid bash -c 'uv run python scripts/eod_babysitter.py > /tmp/eod_babysitter.log 2>&1' &
    tail -f /tmp/eod_babysitter.log

Requires an already-authenticated Gateway (run `scripts/ibkr_gateway_login.py` first). If the
session drops mid-wait it logs a loud `ALARM` — re-run the login (a fresh SMS) and relaunch this.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GW = (os.environ.get("IBKR_CP_GATEWAY_URL", "").strip() or "https://localhost:5000/v1/api").rstrip(
    "/"
)
_CAPTURE_LAG_MIN = 20  # fire this many minutes after the close, so closing prints have settled
_TICKLE_SECONDS = 90

_client = httpx.Client(verify=False, timeout=12)


def _log(msg: str) -> None:
    print(f"[{datetime.now(UTC):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def _auth_ok() -> bool:
    try:
        d = _client.get(_GW + "/iserver/auth/status").json()
        return bool(d.get("authenticated") and d.get("connected"))
    except Exception as e:  # noqa: BLE001 — heartbeat must never crash the loop
        _log(f"auth check error: {e}")
        return False


def _tickle() -> None:
    try:
        _client.post(_GW + "/tickle")
    except Exception as e:  # noqa: BLE001
        _log(f"tickle error: {e}")


def _planned_fires() -> list[tuple[str, datetime]]:
    """(index_symbol, fire_time_utc) for every enabled index trading today, from its calendar."""
    from datetime import timedelta

    from algotrading.core.config.loader import load_platform_config
    from algotrading.infra.universe import (
        CalendarResolver,
        enabled_indices,
        index_registry_from_config,
    )

    config = load_platform_config(_REPO_ROOT / "configs")
    registry = index_registry_from_config(config)
    resolver = CalendarResolver(registry)
    today = datetime.now(UTC).date()
    fires: list[tuple[str, datetime]] = []
    for entry in enabled_indices(registry):
        if not resolver.is_session(entry.symbol, today):
            _log(f"{entry.symbol}: not a session today — skipped")
            continue
        close = resolver.session_close(entry.symbol, today)  # tz-aware UTC
        fires.append((entry.symbol, close + timedelta(minutes=_CAPTURE_LAG_MIN)))
    return sorted(fires, key=lambda x: x[1])


def _fire(index: str) -> None:
    _log(f"=== FIRING EOD capture: {index} ===")
    env = dict(os.environ, IBKR_CP_GATEWAY="1")
    try:
        r = subprocess.run(
            ["uv", "run", "python", "scripts/eod_run.py", "--index", index],
            cwd=str(_REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
            timeout=900,
        )
        _log(f"{index} exit={r.returncode}")
        _log(f"{index} stdout tail:\n{(r.stdout or '')[-700:]}")
        if r.returncode != 0:
            _log(f"{index} stderr tail:\n{(r.stderr or '')[-900:]}")
        Path(f"/tmp/eod_result_{index}.txt").write_text(
            f"exit={r.returncode}\n=STDOUT=\n{(r.stdout or '')[-4000:]}\n=STDERR=\n{(r.stderr or '')[-2000:]}"
        )
    except Exception as e:  # noqa: BLE001
        _log(f"{index} FIRE EXCEPTION: {e}")
        Path(f"/tmp/eod_result_{index}.txt").write_text(f"EXCEPTION {e}")


def main() -> int:
    fires = _planned_fires()
    if not fires:
        _log("no enabled index trades today — nothing to do.")
        return 0
    _log("babysitter up. fires: " + ", ".join(f"{n}@{t:%H:%M}Z" for n, t in fires))
    end = max(t for _, t in fires)
    done: set[str] = set()
    while datetime.now(UTC) <= end:
        if _auth_ok():
            _tickle()
        else:
            _log("ALARM: session NOT authenticated — re-run scripts/ibkr_gateway_login.py (new SMS)")
        for name, ft in fires:
            if name not in done and datetime.now(UTC) >= ft:
                _fire(name)
                done.add(name)
        if len(done) == len(fires):
            break
        time.sleep(_TICKLE_SECONDS)
    _log("babysitter exit. captured=" + ",".join(sorted(done)))
    return 0 if len(done) == len(fires) else 1


if __name__ == "__main__":
    sys.exit(main())
