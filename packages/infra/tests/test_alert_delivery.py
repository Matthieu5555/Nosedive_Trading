from __future__ import annotations

import json

import httpx
import pytest
from algotrading.infra.orchestration import (
    ALERT_QC_FAIL,
    ALERT_SEVERITY_CRITICAL,
    ALERT_SEVERITY_WARNING,
    WEBHOOK_URL_ENV_VAR,
    Alert,
    AlertSink,
    DeliveryResult,
    JournaldAlertSink,
    WebhookAlertSink,
    deliver_alerts,
    resolve_alert_sink,
    severity_for,
)
from algotrading.infra.orchestration.alert_delivery import WEBHOOK_TIMEOUT_ENV_VAR


def _alert(kind: str = ALERT_QC_FAIL) -> Alert:
    return Alert(
        kind=kind,
        subject="run-2026-06-17",
        detail="QC report escalated to page (3 fail(s))",
        detection_interval_seconds=0.0,
    )


def test_sinks_satisfy_the_port_protocol() -> None:
    assert isinstance(JournaldAlertSink(), AlertSink)
    assert isinstance(WebhookAlertSink(url="https://example.invalid/hook"), AlertSink)


def test_severity_classifies_critical_and_warning_kinds() -> None:
    assert severity_for(_alert("qc_fail")) == ALERT_SEVERITY_CRITICAL
    assert severity_for(_alert("collector_death")) == ALERT_SEVERITY_CRITICAL
    assert severity_for(_alert("coverage_breach")) == ALERT_SEVERITY_WARNING
    assert severity_for(_alert("missing_partition")) == ALERT_SEVERITY_WARNING


def test_webhook_sink_formats_and_targets_correctly() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sink = WebhookAlertSink(url="https://hooks.example/test", client=client)

    result = sink.deliver(_alert(), {"correlation_id": "abc123"})

    assert seen["url"] == "https://hooks.example/test"
    assert seen["method"] == "POST"
    body = seen["body"]
    assert isinstance(body, dict)
    assert body["kind"] == ALERT_QC_FAIL
    assert body["severity"] == ALERT_SEVERITY_CRITICAL
    assert body["subject"] == "run-2026-06-17"
    assert body["detail"] == "QC report escalated to page (3 fail(s))"
    assert body["context"] == {"correlation_id": "abc123"}

    assert result == DeliveryResult(
        alert_kind=ALERT_QC_FAIL,
        channel="webhook",
        delivered=True,
        degraded=False,
        detail="delivered via webhook (HTTP 200)",
    )


def test_webhook_delivery_failure_surfaces_and_is_not_swallowed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sink = WebhookAlertSink(url="https://hooks.example/down", client=client)

    result = sink.deliver(_alert())

    assert result.delivered is False
    assert result.degraded is False
    assert result.channel == "webhook"
    assert "failed" in result.detail.lower()


def test_webhook_transport_error_surfaces_as_failed_not_delivered() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sink = WebhookAlertSink(url="https://hooks.example/refused", client=client)

    result = sink.deliver(_alert())

    assert result.delivered is False
    assert result.degraded is False


def test_no_credentials_degrades_to_journald_honestly() -> None:
    sink = resolve_alert_sink(env={})
    assert isinstance(sink, JournaldAlertSink)
    assert sink.channel == "journald"

    result = sink.deliver(_alert(), {"correlation_id": "deadbeef"})
    assert result.delivered is False
    assert result.degraded is True
    assert result.channel == "journald"
    assert WEBHOOK_URL_ENV_VAR in result.detail


def test_resolve_returns_webhook_when_url_is_present() -> None:
    sink = resolve_alert_sink(env={WEBHOOK_URL_ENV_VAR: "https://hooks.example/live"})
    assert isinstance(sink, WebhookAlertSink)
    assert sink.url == "https://hooks.example/live"


def test_resolve_honors_explicit_timeout() -> None:
    sink = resolve_alert_sink(
        env={WEBHOOK_URL_ENV_VAR: "https://hooks.example/live", WEBHOOK_TIMEOUT_ENV_VAR: "2.5"}
    )
    assert isinstance(sink, WebhookAlertSink)
    assert sink.timeout_seconds == pytest.approx(2.5)


def test_resolve_degrades_on_unparseable_timeout() -> None:
    sink = resolve_alert_sink(
        env={WEBHOOK_URL_ENV_VAR: "https://hooks.example/live", WEBHOOK_TIMEOUT_ENV_VAR: "soon"}
    )
    assert isinstance(sink, JournaldAlertSink)
    assert WEBHOOK_TIMEOUT_ENV_VAR in sink.reason


def test_blank_url_degrades_not_treated_as_configured() -> None:
    sink = resolve_alert_sink(env={WEBHOOK_URL_ENV_VAR: "   "})
    assert isinstance(sink, JournaldAlertSink)


def test_deliver_alerts_skips_none_and_returns_one_result_per_firing() -> None:
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    sink = WebhookAlertSink(url="https://hooks.example/batch", client=client)

    results = deliver_alerts(sink, (None, _alert("qc_fail"), None, _alert("coverage_breach")))

    assert [r.alert_kind for r in results] == ["qc_fail", "coverage_breach"]
    assert all(r.delivered for r in results)


def test_deliver_alerts_through_journald_marks_each_degraded() -> None:
    sink = JournaldAlertSink(reason="test")
    results = deliver_alerts(sink, (_alert(), _alert("coverage_breach")))
    assert len(results) == 2
    assert all(r.degraded and not r.delivered for r in results)
