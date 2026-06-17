"""The babysitter's SSO-death path EMITS the IBKR-reauth-needed alert through the C4 seam.

Clock 3 (SSO expiry / a competing session) is the one the babysitter cannot self-heal: it logs a
loud local ALARM AND must push the reauth-needed event through the shared alert-delivery seam so a
human is paged for the manual SMS re-login. This proves the wiring at unit level: a dead session +
a recording ``AlertSink`` ⇒ exactly one reauth alert delivered, classified critical, and a sink
that raises does NOT kill the heartbeat (alerting must never crash the loop).

Oracle: the alert kind, subject, and critical classification are derived from the pure builder
``ibkr_reauth_needed_alert`` and the C4 ``_CRITICAL_KINDS`` set — independent of the babysitter.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from algotrading.infra.orchestration.alert_delivery import (
    ALERT_SEVERITY_CRITICAL,
    DeliveryResult,
    severity_for,
)
from algotrading.infra.orchestration.alerts import (
    ALERT_IBKR_REAUTH_NEEDED,
    Alert,
    ibkr_reauth_needed_alert,
)
from algotrading.infra_ibkr.babysitter import _heartbeat
from algotrading.infra_ibkr.connectivity.cp_rest_session import CpRestSession


class _DeadSession:
    """A CP session that is down and unrevivable: no auth, no establish, revive is a no-op."""

    def established(self) -> bool:
        return False

    def authenticated(self) -> bool:
        return False

    def reauthenticate(self) -> None:  # the one revive attempt does nothing — stays dead
        return None

    def tickle(self) -> bool:
        return True


class _RecordingSink:
    """An AlertSink that records every delivered alert instead of pushing it anywhere."""

    channel = "recording"

    def __init__(self) -> None:
        self.delivered: list[Alert] = []

    def deliver(self, alert: Alert, context: Mapping[str, str] | None = None) -> DeliveryResult:
        self.delivered.append(alert)
        return DeliveryResult(
            alert_kind=alert.kind,
            channel=self.channel,
            delivered=True,
            degraded=False,
            detail="recorded",
        )


class _RaisingSink:
    """An AlertSink whose deliver() raises — proves alerting can never crash the loop."""

    channel = "raising"

    def deliver(self, alert: Alert, context: Mapping[str, str] | None = None) -> DeliveryResult:
        raise RuntimeError("delivery transport exploded")


def _session(obj: object) -> CpRestSession:
    return cast(CpRestSession, obj)


def test_sso_death_emits_reauth_alert_through_the_sink() -> None:
    sink = _RecordingSink()
    alarmed = _heartbeat(_session(_DeadSession()), alarmed=False, sink=sink)

    assert alarmed is True  # the loud-ALARM-and-push path fired
    assert len(sink.delivered) == 1
    alert = sink.delivered[0]
    assert alert.kind == ALERT_IBKR_REAUTH_NEEDED
    assert alert.subject == "ibkr-cp-gateway"
    assert "scripts/ibkr_login.py" in alert.detail


def test_emitted_reauth_alert_is_classified_critical() -> None:
    # Built independently of the babysitter, then run through the C4 severity classifier.
    assert severity_for(ibkr_reauth_needed_alert()) == ALERT_SEVERITY_CRITICAL


def test_alert_not_repeated_once_already_alarmed() -> None:
    sink = _RecordingSink()
    # Already alarmed: the heartbeat stays hands-off and delivers nothing further.
    alarmed = _heartbeat(_session(_DeadSession()), alarmed=True, sink=sink)
    assert alarmed is True
    assert sink.delivered == []


def test_delivery_exception_never_crashes_the_loop() -> None:
    # A sink that raises must be swallowed: the heartbeat still returns the alarmed state, the
    # babysitter loop keeps running.
    alarmed = _heartbeat(_session(_DeadSession()), alarmed=False, sink=_RaisingSink())
    assert alarmed is True
