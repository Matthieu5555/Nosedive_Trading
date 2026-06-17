from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx
import structlog
from algotrading.core.paths import load_env_file

from .alerts import Alert

__all__ = [
    "ALERT_SEVERITY_CRITICAL",
    "ALERT_SEVERITY_WARNING",
    "AlertSink",
    "DeliveryResult",
    "JournaldAlertSink",
    "WEBHOOK_URL_ENV_VAR",
    "WebhookAlertSink",
    "deliver_alerts",
    "resolve_alert_sink",
    "severity_for",
]

WEBHOOK_URL_ENV_VAR = "ALGOTRADING_ALERT_WEBHOOK_URL"
WEBHOOK_TIMEOUT_ENV_VAR = "ALGOTRADING_ALERT_WEBHOOK_TIMEOUT_SECONDS"

ALERT_SEVERITY_CRITICAL = "critical"
ALERT_SEVERITY_WARNING = "warning"

_CRITICAL_KINDS = frozenset(
    {
        "collector_death",
        "qc_fail",
        "elevated_failure_rate",
    }
)

_LOGGER = structlog.get_logger("orchestration.alert_delivery")


def severity_for(alert: Alert) -> str:
    return ALERT_SEVERITY_CRITICAL if alert.kind in _CRITICAL_KINDS else ALERT_SEVERITY_WARNING


@dataclass(frozen=True, slots=True)
class DeliveryResult:

    alert_kind: str
    channel: str
    delivered: bool
    degraded: bool
    detail: str


@runtime_checkable
class AlertSink(Protocol):

    @property
    def channel(self) -> str: ...

    def deliver(self, alert: Alert, context: Mapping[str, str] | None = None) -> DeliveryResult: ...


def _payload(alert: Alert, context: Mapping[str, str] | None) -> dict[str, object]:
    return {
        "kind": alert.kind,
        "severity": severity_for(alert),
        "subject": alert.subject,
        "detail": alert.detail,
        "detection_interval_seconds": alert.detection_interval_seconds,
        "context": dict(context) if context else {},
    }


@dataclass(frozen=True, slots=True)
class JournaldAlertSink:

    reason: str = "no delivery channel configured"
    _log: structlog.stdlib.BoundLogger = field(
        default_factory=lambda: _LOGGER, repr=False, compare=False
    )

    @property
    def channel(self) -> str:
        return "journald"

    def deliver(self, alert: Alert, context: Mapping[str, str] | None = None) -> DeliveryResult:
        self._log.error(
            "orchestration.alert.journald",
            alert_kind=alert.kind,
            severity=severity_for(alert),
            subject=alert.subject,
            detail=alert.detail,
            degraded_reason=self.reason,
            **(dict(context) if context else {}),
        )
        return DeliveryResult(
            alert_kind=alert.kind,
            channel="journald",
            delivered=False,
            degraded=True,
            detail=f"journald-only ({self.reason})",
        )


@dataclass(frozen=True, slots=True)
class WebhookAlertSink:

    url: str
    timeout_seconds: float = 10.0
    client: httpx.Client | None = field(default=None, repr=False, compare=False)

    @property
    def channel(self) -> str:
        return "webhook"

    def deliver(self, alert: Alert, context: Mapping[str, str] | None = None) -> DeliveryResult:
        payload = _payload(alert, context)
        try:
            if self.client is not None:
                response = self.client.post(self.url, json=payload, timeout=self.timeout_seconds)
            else:
                response = httpx.post(self.url, json=payload, timeout=self.timeout_seconds)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _LOGGER.error(
                "orchestration.alert.webhook_failed",
                alert_kind=alert.kind,
                subject=alert.subject,
                error=str(exc),
            )
            return DeliveryResult(
                alert_kind=alert.kind,
                channel="webhook",
                delivered=False,
                degraded=False,
                detail=f"webhook POST failed: {exc}",
            )
        return DeliveryResult(
            alert_kind=alert.kind,
            channel="webhook",
            delivered=True,
            degraded=False,
            detail=f"delivered via webhook (HTTP {response.status_code})",
        )


def resolve_alert_sink(
    env: Mapping[str, str] | None = None,
    *,
    client: httpx.Client | None = None,
) -> AlertSink:
    if env is None:
        load_env_file(os.path.expanduser("~/.env"))
        env = os.environ
    url = env.get(WEBHOOK_URL_ENV_VAR, "").strip()
    if not url:
        return JournaldAlertSink(reason=f"{WEBHOOK_URL_ENV_VAR} unset")
    timeout_raw = env.get(WEBHOOK_TIMEOUT_ENV_VAR, "").strip()
    try:
        timeout = float(timeout_raw) if timeout_raw else 10.0
    except ValueError:
        return JournaldAlertSink(reason=f"{WEBHOOK_TIMEOUT_ENV_VAR} not a number: {timeout_raw!r}")
    return WebhookAlertSink(url=url, timeout_seconds=timeout, client=client)


def deliver_alerts(
    sink: AlertSink,
    alerts: tuple[Alert | None, ...] | list[Alert | None],
    context: Mapping[str, str] | None = None,
) -> list[DeliveryResult]:
    results: list[DeliveryResult] = []
    for alert in alerts:
        if alert is None:
            continue
        results.append(sink.deliver(alert, context))
    return results
