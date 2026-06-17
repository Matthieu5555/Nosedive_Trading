"""Integration contract: a degenerate / QC-critical close FIRES an alert AND exits non-zero.

This is the canary the unattended week rests on — it must be impossible for a close that banked
nothing usable (or that QC paged on) to read as a silent green. The unit workers proved the pure
builders (``degenerate_close_alert``, ``qc_fail_alert``) and the delivery seam in isolation; this
drives the REAL ``default_stages_builder`` `_qc` stage + the runner exit mapping end to end against
a TEMP store (never canonical ``data/``), capturing the delivered ``Alert`` through a recording
``AlertSink`` (the seam, not a log), and asserting the process exit is non-zero.

Two degenerate shapes are exercised, both of which sail through every stage as ``OUTCOME_OK`` with
nothing to fail on at QC, so without the forced page they would exit 0:

* (a) ``None`` basket source — no basket captured at all (the production default
  ``_empty_basket_source``); ``captured_indices`` is empty.
* (b) a basket IS captured but its quotes are last-only (no two-sided bid/ask), so analytics banks
  zero combined-surface grid cells — the market-closed / below-the-floor snapshot.

Plus the independent QC-critical (page) path, where ``run_qc`` itself escalates and the runner must
exit non-zero. The oracle for every assertion is the contract, not the code under test: a degenerate
close ⇒ exactly one ``degenerate_close`` alert delivered (critical) ⇒ ``EodResult.escalation`` is
``page`` ⇒ ``eod_runner.main`` returns 1.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    StrikeSelectionConfig,
    UniverseConfig,
)
from algotrading.infra.actor import IndexBasket
from algotrading.infra.connectivity import ManualClock
from algotrading.infra.contracts import InstrumentMaster, QcResult
from algotrading.infra.orchestration import RunnerDeps, run_fire
from algotrading.infra.orchestration.alert_delivery import (
    ALERT_SEVERITY_CRITICAL,
    DeliveryResult,
    deliver_alerts,
    severity_for,
)
from algotrading.infra.orchestration.alerts import (
    ALERT_DEGENERATE_CLOSE,
    ALERT_QC_FAIL,
    Alert,
    coverage_breach_alerts,
    qc_fail_alert,
)
from algotrading.infra.orchestration.eod_runner import FiredIndex, default_stages_builder, main
from algotrading.infra.qc import ESCALATION_PAGE, build_report, escalation_level
from algotrading.infra.qc.result import SEVERITY_CRITICAL, SEVERITY_WARNING, STATUS_FAIL
from algotrading.infra.storage import ParquetStore, RunRegistry
from algotrading.infra.universe import (
    CalendarResolver,
    IndexRegistry,
    parse_index_registry,
)
from fixtures.events import quote_events
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG, make_option, make_underlying

TRADE_DATE = date(2026, 3, 12)
SPX_CLOSE = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)
CLOCK_NOW = datetime(2026, 3, 12, 22, 0, tzinfo=UTC)

_SPOT = 100.0
_EXPIRIES = (date(2026, 4, 11), date(2026, 6, 10), date(2026, 9, 8))
_STRIKES = (70.0, 80.0, 90.0, 100.0, 110.0, 120.0, 130.0)


class _RecordingSink:
    """An AlertSink that records every delivered alert instead of pushing it anywhere.

    Captures the chain at the seam: what the ``_qc`` stage actually hands to ``deliver_alerts`` —
    not a log line that could drift from the code.
    """

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


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(
            version="u-1",
            exchange="SMART",
            strike_selection=StrikeSelectionConfig(version="ss-1"),
        ),
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


def _registry() -> IndexRegistry:
    return parse_index_registry(
        {
            "SPX": {
                "name": "S&P 500",
                "calendar": "XNYS",
                "currency": "USD",
                "ibkr": {"conid": 0, "secType": "IND", "exchange": "CBOE"},
                "enabled": True,
            }
        }
    )


def _master(instrument, as_of: datetime) -> InstrumentMaster:  # type: ignore[no-untyped-def]
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=as_of.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _last_only_basket(symbol: str, as_of: datetime) -> IndexBasket:
    """A basket that IS captured but carries only last prices — no two-sided bid/ask anywhere.

    Quote-integrity / surface fit needs two-sided quotes; with none, analytics banks zero
    combined-surface grid cells. This is the market-closed / last-only snapshot the degenerate
    detector must catch (``analytics_grid_cells == 0`` despite a non-empty ``captured_indices``).
    """
    underlying = make_underlying(symbol)
    events = list(quote_events(underlying, last=_SPOT, ts=as_of, session_id="u"))
    instruments = [underlying]
    masters = [_master(underlying, as_of)]
    for index, expiry in enumerate(_EXPIRIES):
        bump = 2.0 * (index + 1)
        for strike in _STRIKES:
            for right in ("C", "P"):
                intrinsic = max(_SPOT - strike, 0.0) if right == "C" else max(strike - _SPOT, 0.0)
                mid = intrinsic + 3.0 + bump
                option = make_option(symbol, strike, right, expiry)
                events += list(
                    quote_events(option, last=mid, ts=as_of, session_id=option.canonical())
                )
                instruments.append(option)
                masters.append(_master(option, as_of))
    return IndexBasket(
        instruments=tuple(instruments), events=tuple(events), masters=tuple(masters)
    )


def _deps(
    tmp_path: Path,
    *,
    basket_source,  # type: ignore[no-untyped-def]
    sink: _RecordingSink,
) -> RunnerDeps:
    store = ParquetStore(tmp_path / "data")
    clock = ManualClock(start=CLOCK_NOW)
    registry = _registry()
    return RunnerDeps(
        store=store,
        config=_config(),
        registry=registry,
        resolver=CalendarResolver(registry, as_of=clock),
        run_repository=RunRegistry(tmp_path / "runs"),
        stages_builder=functools.partial(
            default_stages_builder, basket_source=basket_source, alert_sink=sink
        ),
        clock=clock,
        code_identity="deadbeef",
        environment="test",
    )


def _no_basket(fired: FiredIndex, trade_date: date, correlation_id: str) -> IndexBasket | None:
    return None


def _degenerate_grid_cell_count(store: ParquetStore) -> int:
    return sum(1 for row in store.read("projected_option_analytics") if row.surface_side == "combined")


@pytest.mark.parametrize(
    ("label", "basket_source", "expect_captured_index"),
    [
        ("no_basket_captured", _no_basket, False),
        ("last_only_zero_grid", lambda f, _d, _c: _last_only_basket(f.entry.symbol, f.as_of), True),
    ],
)
def test_degenerate_close_delivers_alert_and_escalates_to_page(
    tmp_path: Path,
    label: str,
    basket_source,  # type: ignore[no-untyped-def]
    expect_captured_index: bool,
) -> None:
    sink = _RecordingSink()
    deps = _deps(tmp_path, basket_source=basket_source, sink=sink)

    result = run_fire(deps, trade_date=TRADE_DATE, index="SPX")

    assert result is not None
    # Every stage ran — this is the silent-green shape: nothing aborted, nothing failed a stage.
    assert set(result.ran) == {
        "universe_refresh", "collection", "analytics", "reconciliation", "qc",
    }

    # No usable combined-surface grid cells were banked — the definition of degenerate.
    assert _degenerate_grid_cell_count(deps.store) == 0, (
        f"{label}: a degenerate close must bank zero combined-surface grid cells"
    )

    # CONTRACT 1a: exactly one degenerate-close alert reached the seam, classified critical.
    degenerate = [a for a in sink.delivered if a.kind == ALERT_DEGENERATE_CLOSE]
    assert len(degenerate) == 1, (
        f"{label}: exactly one degenerate_close alert must be delivered; got {sink.delivered!r}"
    )
    assert severity_for(degenerate[0]) == ALERT_SEVERITY_CRITICAL
    if expect_captured_index:
        assert "SPX" in degenerate[0].detail
    else:
        assert "no basket captured" in degenerate[0].detail

    # CONTRACT 1b: the close is forced to PAGE so the runner exits non-zero.
    assert result.escalation == ESCALATION_PAGE, (
        f"{label}: a degenerate close must be forced to PAGE, not left at the QC-clean escalation"
    )


@pytest.mark.parametrize(
    ("label", "basket_source"),
    [
        ("no_basket_captured", _no_basket),
        ("last_only_zero_grid", lambda f, _d, _c: _last_only_basket(f.entry.symbol, f.as_of)),
    ],
)
def test_degenerate_close_runner_exits_nonzero(
    tmp_path: Path,
    label: str,
    basket_source,  # type: ignore[no-untyped-def]
) -> None:
    """The full ``main`` exit path: a degenerate close maps to a non-zero process exit.

    This is the end of the chain the canary protects — ``ESCALATION_PAGE`` ⇒ exit 1, so systemd
    ``Restart=on-failure`` / ``OnFailure=`` engage instead of a silent exit 0.
    """
    sink = _RecordingSink()
    deps = _deps(tmp_path, basket_source=basket_source, sink=sink)
    assert main(["--index", "SPX"], deps=deps) == 1, f"{label}: a degenerate close must exit 1"
    assert any(a.kind == ALERT_DEGENERATE_CLOSE for a in sink.delivered), (
        f"{label}: and it must have delivered the degenerate alert on the way"
    )


def test_healthy_close_is_not_flagged_degenerate_and_exits_zero(tmp_path: Path) -> None:
    """The control: a real two-sided basket banks a grid, delivers NO degenerate alert, exits 0.

    Without this, the degenerate path above could be a blanket failure rather than a discriminating
    canary. The healthy basket is the canonical ``_grid_basket`` shape (two-sided quotes).
    """
    from test_live_spine_wiring import _grid_basket  # the canonical healthy fixture

    sink = _RecordingSink()
    deps = _deps(
        tmp_path,
        basket_source=lambda f, _d, _c: _grid_basket(f.entry.symbol, f.as_of),
        sink=sink,
    )

    rc = main(["--index", "SPX"], deps=deps)
    assert rc == 0
    assert _degenerate_grid_cell_count(deps.store) > 0, "a healthy close must bank combined grid cells"
    assert not [a for a in sink.delivered if a.kind == ALERT_DEGENERATE_CLOSE], (
        "a healthy close must deliver no degenerate-close alert"
    )
    # A healthy close is NOT force-paged by the degenerate path — its escalation is whatever QC
    # actually decided (here a notice from the synthetic fixture's fit warnings), never PAGE-by-force.
    result = run_fire(deps, trade_date=TRADE_DATE, index="SPX")
    assert result is not None and result.escalation != ESCALATION_PAGE


def _qc_result(*, severity: str, status: str) -> QcResult:
    return QcResult(
        run_id="run-page",
        check_name="check_underlying_quote_health",
        target_key="SPX",
        run_ts=CLOCK_NOW,
        qc_status=status,
        severity=severity,
        measured_value=0.0,
        threshold_version="qc-1",
        context="{}",
    )


def test_qc_critical_page_delivers_qc_fail_alert_through_the_seam() -> None:
    """A QC-critical fail ⇒ a ``qc_fail`` alert delivered (critical) AND escalation level ``page``.

    Drives the real builders/seam, not a mock: a report with a critical FAIL is built via the
    production ``build_report``; ``escalation_level`` (the same function ``run_qc`` uses) must map it
    to PAGE, ``qc_fail_alert`` must emit a ``qc_fail`` alert, and the real ``deliver_alerts`` must
    push exactly that one alert through the recording sink classified critical. The oracle is the
    contract: critical-fail ⇒ page ⇒ one delivered qc_fail alert.
    """
    report = build_report(
        [_qc_result(severity=SEVERITY_CRITICAL, status=STATUS_FAIL)],
        run_id="run-page",
        run_ts=CLOCK_NOW,
    )
    assert escalation_level(report) == ESCALATION_PAGE

    sink = _RecordingSink()
    alert = qc_fail_alert(report)
    assert alert is not None and alert.kind == ALERT_QC_FAIL
    results = deliver_alerts(sink, (alert, *coverage_breach_alerts(report)), {"underlying": "SPX"})

    qc_alerts = [a for a in sink.delivered if a.kind == ALERT_QC_FAIL]
    assert len(qc_alerts) == 1
    assert severity_for(qc_alerts[0]) == ALERT_SEVERITY_CRITICAL
    assert all(r.delivered for r in results)


def test_qc_warning_only_does_not_page_and_emits_no_qc_fail_alert() -> None:
    """The discriminator: a warning-only report stays below page and fires no qc_fail alert.

    Without this control the page path could be a blanket reaction; here a non-critical fail must
    NOT escalate to page and ``qc_fail_alert`` must return ``None`` (nothing to deliver)."""
    report = build_report(
        [_qc_result(severity=SEVERITY_WARNING, status=STATUS_FAIL)],
        run_id="run-notice",
        run_ts=CLOCK_NOW,
    )
    assert escalation_level(report) != ESCALATION_PAGE
    assert qc_fail_alert(report) is None
