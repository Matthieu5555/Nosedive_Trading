from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from algotrading.infra.orchestration import (
    ALERT_DEGENERATE_CLOSE,
    Alert,
    default_stages_builder,
    degenerate_close_alert,
)
from algotrading.infra.orchestration.alert_delivery import DeliveryResult, severity_for
from algotrading.infra.storage import ParquetStore
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG

_AS_OF = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
_CONFIG_HASH = {"cfg": "cfg-hash-degenerate"}


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
        surface=SURFACE_CONFIG,
        forward=FORWARD_CONFIG,
        scenario=ScenarioConfig(
            version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05, -0.05)
        ),
    )


class _RecordingSink:
    """A test double for C4's AlertSink — records what it was handed."""

    def __init__(self) -> None:
        self.delivered: list[Alert] = []

    @property
    def channel(self) -> str:
        return "recording"

    def deliver(self, alert: Alert, context: Mapping[str, str] | None = None) -> DeliveryResult:
        self.delivered.append(alert)
        return DeliveryResult(
            alert_kind=alert.kind,
            channel=self.channel,
            delivered=True,
            degraded=False,
            detail="recorded",
        )


# --- pure builder ---------------------------------------------------------------------


def test_degenerate_close_fires_when_no_basket_captured() -> None:
    alert = degenerate_close_alert(
        correlation_id="run-x", captured_indices=[], analytics_grid_cells=0
    )
    assert alert is not None
    assert alert.kind == ALERT_DEGENERATE_CLOSE
    assert alert.subject == "run-x"
    assert "no basket captured" in alert.detail


def test_degenerate_close_fires_when_baskets_but_zero_grid_cells() -> None:
    alert = degenerate_close_alert(
        correlation_id="run-y", captured_indices=["SX5E"], analytics_grid_cells=0
    )
    assert alert is not None
    assert "0 combined-surface grid cells" in alert.detail


def test_degenerate_close_silent_when_data_banked() -> None:
    assert (
        degenerate_close_alert(
            correlation_id="run-z", captured_indices=["SX5E"], analytics_grid_cells=42
        )
        is None
    )


def test_degenerate_close_is_critical_severity() -> None:
    # A degenerate close must page, so the C4 delivery layer must classify it critical.
    alert = degenerate_close_alert(
        correlation_id="run-c", captured_indices=[], analytics_grid_cells=0
    )
    assert alert is not None
    assert severity_for(alert) == "critical"


# --- end-to-end: routed through the seam AND escalated to PAGE -------------------------


def test_degenerate_close_alerts_and_escalates_to_page_through_the_seam(tmp_path: Path) -> None:
    # No fired indices -> no basket captured -> degenerate close. The _qc stage must route the
    # degenerate_close alert through the injected sink AND force escalation to PAGE so the runner
    # exits non-zero instead of a silent green.
    from algotrading.infra.connectivity import ManualClock

    store = ParquetStore(tmp_path)
    sink = _RecordingSink()
    stages = default_stages_builder(
        store,
        _config(),
        _CONFIG_HASH,
        ManualClock(start=_AS_OF),
        "corr-degenerate",
        (),
        alert_sink=sink,
    )
    stages.universe_refresh()
    stages.collection()
    stages.analytics()
    job = stages.qc()

    assert job.escalation == "page"
    kinds = [a.kind for a in sink.delivered]
    assert ALERT_DEGENERATE_CLOSE in kinds
    degenerate = next(a for a in sink.delivered if a.kind == ALERT_DEGENERATE_CLOSE)
    assert degenerate.subject == "corr-degenerate"


# --- intraday: a fire before the close skips QC entirely ------------------------------


def test_intraday_fire_skips_qc_entirely(tmp_path: Path) -> None:
    # A manual fire BEFORE the session close (clock < as_of) is intraday: the capture is
    # provisional and legitimately thin, so the _qc stage is skipped wholesale — no qc_results
    # rows, no triage, no alerts, escalation 'none', and the qc stage commits OK (empty report
    # is 'pass'). This is the deliberate contrast to the degenerate-close test above: the SAME
    # zero-grid-cell capture pages AT/AFTER the close but must stay silent intraday. The
    # production systemd timer fires at/after the close, so it is never intraday and is untouched.
    from algotrading.infra.connectivity import ManualClock
    from algotrading.infra.orchestration.eod_runner import FiredIndex
    from algotrading.infra.universe import parse_index_registry

    entry = parse_index_registry(
        {
            "SX5E": {
                "name": "EURO STOXX 50",
                "calendar": "XEUR",
                "currency": "EUR",
                "ibkr": {"conid": 1, "secType": "IND", "exchange": "EUREX"},
                "enabled": True,
            }
        }
    ).get("SX5E")
    before_close = datetime(2026, 5, 29, 12, 0, tzinfo=UTC)  # _AS_OF is 15:30 -> still intraday
    fired = (FiredIndex(entry=entry, as_of=_AS_OF, next_open=_AS_OF),)

    store = ParquetStore(tmp_path)
    sink = _RecordingSink()
    stages = default_stages_builder(
        store,
        _config(),
        _CONFIG_HASH,
        ManualClock(start=before_close),
        "corr-intraday",
        fired,
        alert_sink=sink,
    )
    stages.universe_refresh()
    stages.collection()
    stages.analytics()
    job = stages.qc()

    assert job.escalation == "none"
    assert job.report.overall_status == "pass"
    assert job.report.total == 0
    assert sink.delivered == []  # no QC, coverage, or degenerate alert fires intraday
    assert store.read("qc_results") == []
    assert store.read("triage_records") == []
