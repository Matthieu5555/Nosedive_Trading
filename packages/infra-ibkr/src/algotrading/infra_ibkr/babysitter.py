"""Keep the CP Gateway session warm — and fire each index's EOD capture at its own close.

The importable core behind ``scripts/eod_babysitter.py`` (which is a thin shim over :func:`main`).
One module, two modes, one auth seam. The session handling rides the *tested* package seam
(``session_factory.build_gateway_session`` → ``CpRestSession``) instead of a hand-rolled
``httpx.Client`` + auth check: the canonical check also guards against a *competing* session
(another login took the line), which the old inline ``authenticated and connected`` check silently
treated as healthy.

* **default (fire mode):** in production the systemd timer (ADR 0032) fires ``eod_run.py`` at each
  index's session close. On a box with no timer — e.g. after a manual headless login
  (``scripts/ibkr_gateway_login.py``) — this stands in: it keeps the session warm and fires the
  close-capture for each *enabled* index just after that index's own ``session_close`` (derived
  from its exchange calendar, so it is correct on half-days and across DST). Writes the canonical
  store — the real harvest.
* **--no-fire (keepalive mode):** only the heartbeat, indefinitely (the old ``gateway_keepalive``).

The heartbeat self-heals what it can — idle timeout (``tickle``), brokerage-session drop
(``reauthenticate`` without an SMS) — and on SSO expiry (~daily) logs a loud ``ALARM`` (also routed
to journald via ``systemd-cat``) AND delivers it through the shared C4 alert-delivery seam
(``infra.orchestration.alert_delivery``) so the required manual SMS re-login is a *pushed* alert,
not just a line in ``/tmp/eod_babysitter.log`` nobody reads. The loud local log stays as the
delivery-of-last-resort the seam already falls back to; the seam rides on top, never instead.

Testability: :func:`_babysit` takes its clock (``now``), ``sleep``, the per-index ``fire`` callable,
the ``heartbeat`` callable, the alert ``sink``, and ``planned_fires`` as injected parameters
(defaulting to the real ones), so the fire-loop, its exit code, AND the SSO-death alert emission are
driven deterministically in a test with no wall clock, no real sleep, no subprocess, and a recording
``AlertSink`` — the same dependency-injection discipline ``eod_runner`` holds.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from algotrading.core.paths import repo_root
from algotrading.infra.orchestration.alert_delivery import (
    AlertSink,
    deliver_alerts,
    resolve_alert_sink,
)
from algotrading.infra.orchestration.alerts import ibkr_reauth_needed_alert
from algotrading.infra_ibkr.connectivity.cp_rest_session import CpRestSession
from algotrading.infra_ibkr.connectivity.cp_rest_transport import CpRestTransportError
from algotrading.infra_ibkr.session_factory import build_gateway_session

_REPO_ROOT = repo_root()
_CAPTURE_LAG_MIN = 20  # fire this many minutes after the close, so closing prints have settled
_TICKLE_SECONDS = 60  # well under the idle timeout
_HEARTBEAT_EVERY = 10  # log a positive heartbeat every Nth cycle (~10 min), not every tickle
_REVIVE_GRACE_S = 3  # the reauthenticate trigger needs a moment before the status flips


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _log(msg: str) -> None:
    print(f"[{_now_utc():%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def _default_sink() -> AlertSink:
    """Resolve the C4 alert sink the same way the orchestration code does.

    A factory (not a module-level singleton) so a missing webhook env var is re-read each call and
    so importing the babysitter never touches the network/env at import time. Tests inject a
    recording sink instead.
    """
    return resolve_alert_sink()


def _deliver_reauth_alert(sink: AlertSink) -> None:
    """Push the IBKR SSO-reauth-needed alert through the C4 seam — never raises.

    The local loud ALARM (stdout + journald via :func:`_alarm_to_journald`) is the
    delivery-of-last-resort and has already fired by the time this runs; this ADDS the real pushed
    delivery on top. Alerting must never kill the keepalive loop, so any failure resolving the sink
    or delivering is logged and swallowed — exactly the discipline :func:`_alarm_to_journald` holds.
    """
    try:
        results = deliver_alerts(
            sink,
            (ibkr_reauth_needed_alert(detection_interval_seconds=_TICKLE_SECONDS),),
            {"source": "eod_babysitter", "clock": "sso_expiry"},
        )
        for r in results:
            _log(
                f"reauth alert -> channel={r.channel} delivered={r.delivered} "
                f"degraded={r.degraded} ({r.detail})"
            )
    except Exception as exc:  # noqa: BLE001 — alerting must never kill the loop
        _log(f"(reauth-alert delivery failed, alarm stayed on stdout/journald: {exc})")


def _alarm_to_journald(msg: str) -> None:
    """Best-effort: route a loud ALARM to journald via ``systemd-cat`` so it is visible without a
    terminal attached (the babysitter is not under systemd, so it has no OnFailure= of its own).

    Fire-and-forget — ``check=False`` and a broad guard: if ``systemd-cat`` is absent (a dev box
    with no systemd) the alarm still reaches stdout via :func:`_log`, and alerting must never crash
    the keepalive loop.
    """
    try:
        subprocess.run(
            ["systemd-cat", "-t", "eod-babysitter", "-p", "err"],
            input=msg,
            text=True,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 — alerting must never kill the loop
        _log(f"(systemd-cat unavailable, alarm stayed on stdout: {exc})")


def _heartbeat(
    session: CpRestSession,
    *,
    alarmed: bool,
    sink: AlertSink | None = None,
) -> bool:
    """One keepalive cycle: tickle a healthy session, self-heal a lapsed one, alarm on SSO death.

    Returns the new alarm state (True once the loud ALARM has been logged AND the reauth alert has
    been delivered, so neither is repeated every cycle). Never raises: a transport error (Gateway
    down/restarting) is logged and retried on the next cycle, and alert delivery is wrapped so a
    delivery failure is logged, not fatal.

    ``sink`` is the injected C4 alert sink; when ``None`` it is resolved the same way the
    orchestration code resolves it (webhook if configured, journald otherwise). Tests inject a
    recording sink to prove the SSO-death path emits the reauth alert.
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
            alarm = (
                "ALARM: session DOWN and not revivable (SSO expired or a competing session). "
                "Re-run scripts/ibkr_gateway_login.py for a fresh SMS login."
            )
            _log(alarm)
            _alarm_to_journald(alarm)
            # On top of the loud local log, push the alert through the C4 delivery seam so the
            # required manual SMS re-login reaches the operator (webhook/Telegram/email), not just
            # a log line. Delivery is wrapped so it can never kill the loop.
            _deliver_reauth_alert(sink if sink is not None else _default_sink())
        return True
    except CpRestTransportError as exc:
        _log(f"gateway unreachable: {exc}")
        return alarmed


def _planned_fires(now: Callable[[], datetime] = _now_utc) -> list[tuple[str, datetime]]:
    """(index_symbol, fire_time_utc) for every enabled index trading today, from its calendar.

    ``now`` is injected so a test pins "today" without a wall-clock read.
    """
    from algotrading.core.config.loader import load_platform_config
    from algotrading.infra.universe import (
        CalendarResolver,
        enabled_indices,
        index_registry_from_config,
    )

    config = load_platform_config(_REPO_ROOT / "configs")
    registry = index_registry_from_config(config)
    resolver = CalendarResolver(registry)
    today = now().date()
    fires: list[tuple[str, datetime]] = []
    for entry in enabled_indices(registry):
        if not resolver.is_session(entry.symbol, today):
            _log(f"{entry.symbol}: not a session today — skipped")
            continue
        close = resolver.session_close(entry.symbol, today)  # tz-aware UTC
        fires.append((entry.symbol, close + timedelta(minutes=_CAPTURE_LAG_MIN)))
    return sorted(fires, key=lambda x: x[1])


def _fire(index: str) -> bool:
    """Fire one index's EOD capture. Return ``True`` on a clean (exit 0) capture, else ``False``.

    Never raises — a fire failure must not kill the babysitter loop — but the boolean lets the
    caller track failures and exit non-zero, so an unattended run does not report success after a
    failed capture (the silent-failure gap the 2026-06-15 ingestion audit flagged). A non-zero
    ``eod_run`` exit (e.g. a QC page escalation, now surfaced as a non-zero exit) returns ``False``.
    """
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
        return r.returncode == 0
    except Exception as e:  # noqa: BLE001 — a fire failure must not kill the babysitter loop
        _log(f"{index} FIRE EXCEPTION: {e}")
        Path(f"/tmp/eod_result_{index}.txt").write_text(f"EXCEPTION {e}")
        return False


def _keepalive_forever(
    session: CpRestSession,
    *,
    heartbeat: Callable[..., bool] = _heartbeat,
    sleep: Callable[[float], None] = time.sleep,
    sink: AlertSink | None = None,
) -> int:
    """The --no-fire mode: heartbeat indefinitely (the old gateway_keepalive.py)."""
    _log(f"keepalive up — tickling every {_TICKLE_SECONDS}s.")
    cycle = 0
    alarmed = False
    while True:
        cycle += 1
        alarmed = heartbeat(session, alarmed=alarmed, sink=sink)
        if not alarmed and cycle % _HEARTBEAT_EVERY == 0:
            _log("ok — session alive (heartbeat).")
        sleep(_TICKLE_SECONDS)


def _babysit(
    session: CpRestSession,
    *,
    planned_fires: Callable[[], Sequence[tuple[str, datetime]]] = _planned_fires,
    fire: Callable[[str], bool] = _fire,
    heartbeat: Callable[..., bool] = _heartbeat,
    now: Callable[[], datetime] = _now_utc,
    sleep: Callable[[float], None] = time.sleep,
    sink: AlertSink | None = None,
) -> int:
    """The fire mode: heartbeat until every enabled index's capture has fired.

    Returns ``0`` only when every planned fire ran AND each succeeded; ``1`` when a fire failed or
    a fire's slot was missed (the loop ended before it came due) — an unattended run must never
    report success on a missed or errored capture. All time/IO seams (``now``, ``sleep``, ``fire``,
    ``heartbeat``, ``planned_fires``) are injected so the loop is driven deterministically in tests.
    """
    fires = list(planned_fires())
    if not fires:
        _log("no enabled index trades today — nothing to do.")
        return 0
    _log("babysitter up. fires: " + ", ".join(f"{n}@{t:%H:%M}Z" for n, t in fires))
    end = max(t for _, t in fires)
    done: set[str] = set()
    failed: set[str] = set()
    alarmed = False
    while now() <= end:
        alarmed = heartbeat(session, alarmed=alarmed, sink=sink)
        for name, ft in fires:
            if name not in done and now() >= ft:
                if not fire(name):
                    failed.add(name)
                done.add(name)  # mark fired either way so it is not re-fired this run
        if len(done) == len(fires):
            break
        sleep(_TICKLE_SECONDS)
    captured = sorted(done - failed)
    _log(
        "babysitter exit. captured="
        + ",".join(captured)
        + ("; FAILED=" + ",".join(sorted(failed)) if failed else "")
    )
    # Exit non-zero if any planned fire did not run (timed out before its slot) OR ran but failed —
    # an unattended run must not report success when a capture was missed or errored.
    return 0 if (len(done) == len(fires) and not failed) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--no-fire",
        action="store_true",
        help="keepalive only: heartbeat the Gateway session indefinitely, fire no captures",
    )
    args = parser.parse_args(argv)
    # The session seam, not a hand-rolled client: same base-URL resolution (IBKR_CP_GATEWAY_URL
    # or the localhost default), same self-signed-TLS handling, same tested auth checks. No
    # establish handshake here — the heartbeat itself reauthenticates/alarms as needed.
    _transport, session = build_gateway_session(establish=False)
    if args.no_fire:
        return _keepalive_forever(session)
    return _babysit(session)
