"""Hold the IBKR Client Portal Gateway session alive as long as possible, unattended.

The CP Gateway session has TWO clocks, and this beats only the first:

1. **Idle timeout (minutes).** The session drops after a short idle. `tickle` resets it — so a
   steady heartbeat keeps it up. This script does that.
2. **Brokerage-session drop (`connected:false`, `authenticated:true`).** The SSO cookie is still
   valid but the brokerage session lapsed. `reauthenticate` revives it WITHOUT a new SMS. This
   script self-heals that too.
3. **SSO expiry (~daily).** When the browser-login cookie itself expires, NOTHING here can revive
   it — IBKR requires a fresh browser login (a new SMS to the account phone). The script can't beat
   this; it logs a loud `ALARM` so an operator re-runs `scripts/ibkr_gateway_login.py`.

So this minimizes — but cannot eliminate — the ~daily re-login. The only path to a truly
unattended session (no daily SMS) is the hosted **OAuth 1.0a** path (`IBKR_CP_*`, ADR 0031), which
needs the Self-Service OAuth enrolment; until that lands, expect ~one SMS per day.

Run detached:

    setsid bash -c 'uv run python scripts/gateway_keepalive.py > /tmp/gateway_keepalive.log 2>&1' &
    tail -f /tmp/gateway_keepalive.log
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime

import httpx

_GW = (os.environ.get("IBKR_CP_GATEWAY_URL", "").strip() or "https://localhost:5000/v1/api").rstrip(
    "/"
)
_TICKLE_SECONDS = 60  # well under the idle timeout
_HEARTBEAT_EVERY = 10  # log a positive heartbeat every Nth cycle (~10 min), not every tickle
_client = httpx.Client(verify=False, timeout=12)


def _log(msg: str) -> None:
    print(f"[{datetime.now(UTC):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def _status() -> dict:
    try:
        return _client.get(_GW + "/iserver/auth/status").json()
    except Exception as e:  # noqa: BLE001
        return {"_error": str(e)}


def _post(path: str) -> None:
    try:
        _client.post(_GW + path)
    except Exception as e:  # noqa: BLE001
        _log(f"POST {path} error: {e}")


def main() -> int:
    _log("keepalive up — tickling every %ds." % _TICKLE_SECONDS)
    i = 0
    alarmed = False
    while True:
        i += 1
        s = _status()
        authed, conn = bool(s.get("authenticated")), bool(s.get("connected"))
        if authed and conn:
            _post("/tickle")
            alarmed = False
            if i % _HEARTBEAT_EVERY == 0:
                _log("ok — authenticated, connected (heartbeat).")
        elif authed and not conn:
            _log("brokerage session idle (connected=false) — reauthenticate (no SMS)...")
            _post("/iserver/reauthenticate")
        else:
            # SSO likely expired — try a revive, but this usually needs a fresh browser login (SMS)
            _post("/iserver/reauthenticate")
            time.sleep(3)
            s2 = _status()
            if not (s2.get("authenticated") and s2.get("connected")):
                if not alarmed:
                    _log(
                        "ALARM: session DOWN and not revivable (SSO expired). "
                        "Re-run scripts/ibkr_gateway_login.py for a fresh SMS login. "
                        f"status={s}"
                    )
                    alarmed = True
        time.sleep(_TICKLE_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
