"""Keep the CP Gateway session warm — and fire each index's EOD capture at its own close.

One script, two modes, one auth seam. The session handling rides the *tested* package seam
(`session_factory.build_gateway_session` → `CpRestSession`) instead of a hand-rolled
`httpx.Client` + auth check: the canonical check also guards against a *competing* session
(another login took the line), which the old inline `authenticated and connected` check
silently treated as healthy.

* **default (fire mode):** in production the systemd timer (ADR 0032) fires `eod_run.py` at each
  index's session close. On a box with no timer — e.g. after a manual headless login (see
  `documentation/connectivity/ibkr-gateway-headless-login.md`) — this stands in: it keeps the
  session warm and fires the close-capture for each *enabled* index just after that index's own
  `session_close` (derived from its exchange calendar, so it is correct on half-days and across
  DST). It writes to the canonical store, so this is the real harvest.
* **--no-fire (keepalive mode):** only the heartbeat, indefinitely — the old standalone
  `gateway_keepalive.py`, now folded in here.

The heartbeat self-heals what it can. The CP Gateway session has three clocks:

1. **Idle timeout (minutes).** `tickle` resets it — the steady heartbeat handles this.
2. **Brokerage-session drop** (`authenticated: true, connected: false`). The SSO cookie is still
   valid; `session.reauthenticate()` revives it WITHOUT a new SMS.
3. **SSO expiry (~daily).** Nothing here can revive it — IBKR requires a fresh browser login (a
   new SMS). The script logs a loud `ALARM` so an operator re-runs `scripts/ibkr_gateway_login.py`.

So it minimizes — but cannot eliminate — the ~daily re-login; the only truly unattended path is
the hosted OAuth 1.0a one (`IBKR_CP_*`, ADR 0031). Run detached so it outlives your shell:

    setsid bash -c 'uv run python scripts/eod_babysitter.py > /tmp/eod_babysitter.log 2>&1' &
    setsid bash -c \\
        'uv run python scripts/eod_babysitter.py --no-fire > /tmp/gateway_keepalive.log 2>&1' &
    tail -f /tmp/eod_babysitter.log

Requires an already-authenticated Gateway (run `scripts/ibkr_gateway_login.py` first).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from algotrading.core.paths import repo_root
from algotrading.infra_ibkr.connectivity.cp_rest_session import CpRestSession
from algotrading.infra_ibkr.connectivity.cp_rest_transport import CpRestTransportError
from algotrading.infra_ibkr.session_factory import build_gateway_session

_REPO_ROOT = repo_root()
_CAPTURE_LAG_MIN = 20  # fire this many minutes after the close, so closing prints have settled
_TICKLE_SECONDS = 60  # well under the idle timeout
_HEARTBEAT_EVERY = 10  # log a positive heartbeat every Nth cycle (~10 min), not every tickle
_REVIVE_GRACE_S = 3  # the reauthenticate trigger needs a moment before the status flips


def _log(msg: str) -> None:
    print(f"[{datetime.now(UTC):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def _heartbeat(session: CpRestSession, *, alarmed: bool) -> bool:
    """One keepalive cycle: tickle a healthy session, self-heal a lapsed one, alarm on SSO death.

    Returns the new alarm state (True once the loud ALARM has been logged, so it is not
    repeated every cycle). Never raises: a transport error (Gateway down/restarting) is logged
    and retried on the next cycle.
    """
    try:
        if session.established():
            # Authenticated, non-competing, connected — the healthy path: reset the idle clock.
            session.tickle()
            return False
        if session.authenticated():
            # SSO cookie alive but the brokerage session lapsed — revivable without an SMS.
            _log("brokerage session idle (connected=false) — reauthenticate (no SMS)...")
            session.reauthenticate()
            return False
        # Not authenticated (or a competing session took the line) — try ONE revive, but
        # once the ALARM has fired stay hands-off: each POST /iserver/reauthenticate can
        # kick a competing session off the line, and a real revive needs the operator's
        # SMS login anyway.
        if alarmed:
            return True
        session.reauthenticate()
        time.sleep(_REVIVE_GRACE_S)
        if session.established():
            return False
        if not alarmed:
            _log(
                "ALARM: session DOWN and not revivable (SSO expired or a competing session). "
                "Re-run scripts/ibkr_gateway_login.py for a fresh SMS login."
            )
        return True
    except CpRestTransportError as exc:
        _log(f"gateway unreachable: {exc}")
        return alarmed


def _planned_fires() -> list[tuple[str, datetime]]:
    """(index_symbol, fire_time_utc) for every enabled index trading today, from its calendar."""
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
            f"exit={r.returncode}\n=STDOUT=\n{(r.stdout or '')[-4000:]}"
            f"\n=STDERR=\n{(r.stderr or '')[-2000:]}"
        )
    except Exception as e:  # noqa: BLE001 — a fire failure must not kill the babysitter loop
        _log(f"{index} FIRE EXCEPTION: {e}")
        Path(f"/tmp/eod_result_{index}.txt").write_text(f"EXCEPTION {e}")


def _keepalive_forever(session: CpRestSession) -> int:
    """The --no-fire mode: heartbeat indefinitely (the old gateway_keepalive.py)."""
    _log(f"keepalive up — tickling every {_TICKLE_SECONDS}s.")
    cycle = 0
    alarmed = False
    while True:
        cycle += 1
        alarmed = _heartbeat(session, alarmed=alarmed)
        if not alarmed and cycle % _HEARTBEAT_EVERY == 0:
            _log("ok — session alive (heartbeat).")
        time.sleep(_TICKLE_SECONDS)


def _babysit(session: CpRestSession) -> int:
    """The fire mode: heartbeat until every enabled index's capture has fired."""
    fires = _planned_fires()
    if not fires:
        _log("no enabled index trades today — nothing to do.")
        return 0
    _log("babysitter up. fires: " + ", ".join(f"{n}@{t:%H:%M}Z" for n, t in fires))
    end = max(t for _, t in fires)
    done: set[str] = set()
    alarmed = False
    while datetime.now(UTC) <= end:
        alarmed = _heartbeat(session, alarmed=alarmed)
        for name, ft in fires:
            if name not in done and datetime.now(UTC) >= ft:
                _fire(name)
                done.add(name)
        if len(done) == len(fires):
            break
        time.sleep(_TICKLE_SECONDS)
    _log("babysitter exit. captured=" + ",".join(sorted(done)))
    return 0 if len(done) == len(fires) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-fire",
        action="store_true",
        help="keepalive only: heartbeat the Gateway session indefinitely, fire no captures",
    )
    args = parser.parse_args()
    # The session seam, not a hand-rolled client: same base-URL resolution (IBKR_CP_GATEWAY_URL
    # or the localhost default), same self-signed-TLS handling, same tested auth checks. No
    # establish handshake here — the heartbeat itself reauthenticates/alarms as needed.
    _transport, session = build_gateway_session(establish=False)
    if args.no_fire:
        return _keepalive_forever(session)
    return _babysit(session)


if __name__ == "__main__":
    sys.exit(main())
