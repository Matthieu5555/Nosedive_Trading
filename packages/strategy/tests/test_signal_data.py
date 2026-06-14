"""The store-backed SignalSnapshot reader — and S1 going live off the persisted signal layer.

Writes ``strategy_signals`` rows to a temp store, reads them back into a ``SignalSnapshot`` via
:func:`signal_snapshot_from_store`, and checks the snapshot the strategy sees. Also pins the
cross-layer seam (the infra ``signal_kind`` strings must equal the strategy ``SignalKind``
values) and drives the real :class:`DispersionStrategy` off a store-sourced snapshot.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from algotrading.infra.contracts import StrategySignal
from algotrading.infra.signals import (
    SIGNAL_KIND_IMPLIED_CORRELATION,
    SIGNAL_KIND_IV_RANK,
    SIGNAL_KIND_IV_VS_REALIZED,
    SIGNAL_KIND_TERM_STRUCTURE_SLOPE,
)
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import BasketMember
from algotrading.strategy import (
    DispersionConfig,
    DispersionStrategy,
    EntryAction,
    SignalKind,
)
from algotrading.strategy.signal_data import signal_snapshot_from_store
from fixtures.records import make_stamp

PROVIDER = "IBKR"
INDEX = "SX5E"
D0 = date(2026, 5, 29)
TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)


def _signal(signal_kind: str, subject: str, tenor_label: str, value: float) -> StrategySignal:
    return StrategySignal(
        snapshot_ts=TS,
        provider=PROVIDER,
        underlying=INDEX,
        signal_kind=signal_kind,
        subject=subject,
        tenor_label=tenor_label,
        value=value,
        source_snapshot_ts=TS,
        provenance=make_stamp(),
    )


def _seed(store: ParquetStore) -> None:
    store.write(
        "strategy_signals",
        [
            _signal(SIGNAL_KIND_IMPLIED_CORRELATION, INDEX, "3m", 0.62),
            _signal(SIGNAL_KIND_IMPLIED_CORRELATION, INDEX, "6m", 0.71),  # off reference tenor
            _signal(SIGNAL_KIND_IV_RANK, "AAA", "3m", 0.5),
            _signal(SIGNAL_KIND_IV_RANK, "BBB", "3m", 0.8),
            _signal(SIGNAL_KIND_IV_VS_REALIZED, "AAA", "3m", 0.03),
            _signal(SIGNAL_KIND_TERM_STRUCTURE_SLOPE, "AAA", "1m:6m", 0.03),
        ],
    )


def test_reference_tenor_correlation_is_surfaced_for_the_index(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    _seed(store)
    snapshot = signal_snapshot_from_store(
        store, D0, index=INDEX, provider=PROVIDER, reference_tenor="3m"
    )
    reading = snapshot.latest(SignalKind.IMPLIED_CORRELATION, subject=INDEX)
    assert reading is not None
    assert reading.value == 0.62  # the 3m reading, not the 6m one


def test_off_reference_tenor_readings_are_not_surfaced(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    _seed(store)
    snapshot = signal_snapshot_from_store(
        store, D0, index=INDEX, provider=PROVIDER, reference_tenor="3m"
    )
    # Exactly one correlation reading reaches the snapshot (the 6m one is filtered out), so
    # ``latest`` is unambiguous.
    assert len(snapshot.all_of(SignalKind.IMPLIED_CORRELATION)) == 1


def test_per_name_readings_keep_their_subject(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    _seed(store)
    snapshot = signal_snapshot_from_store(
        store, D0, index=INDEX, provider=PROVIDER, reference_tenor="3m"
    )
    ranks = {r.subject: r.value for r in snapshot.all_of(SignalKind.IV_RANK)}
    assert ranks == {"AAA": 0.5, "BBB": 0.8}


def test_term_slope_is_surfaced_despite_its_composite_tenor(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    _seed(store)
    snapshot = signal_snapshot_from_store(
        store, D0, index=INDEX, provider=PROVIDER, reference_tenor="3m"
    )
    slopes = snapshot.all_of(SignalKind.TERM_STRUCTURE_SLOPE)
    assert len(slopes) == 1
    assert slopes[0].subject == "AAA"


def test_empty_day_yields_an_empty_snapshot(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    _seed(store)
    snapshot = signal_snapshot_from_store(
        store, date(2026, 1, 1), index=INDEX, provider=PROVIDER, reference_tenor="3m"
    )
    assert snapshot.readings == ()
    assert snapshot.as_of == date(2026, 1, 1)


def test_signal_kind_strings_pin_the_cross_layer_seam() -> None:
    # Infra is blind to alpha: it mirrors the SignalKind values as plain strings. This pins them
    # so the seam cannot silently drift.
    assert SignalKind.IMPLIED_CORRELATION.value == SIGNAL_KIND_IMPLIED_CORRELATION
    assert SignalKind.IV_RANK.value == SIGNAL_KIND_IV_RANK
    assert SignalKind.IV_VS_REALIZED.value == SIGNAL_KIND_IV_VS_REALIZED
    assert SignalKind.TERM_STRUCTURE_SLOPE.value == SIGNAL_KIND_TERM_STRUCTURE_SLOPE


def test_s1_enters_off_a_store_sourced_snapshot(tmp_path: Path) -> None:
    # End-to-end: the persisted ρ̄ (0.62) drives the real strategy's entry over the 0.55 bar —
    # the seam S1 was built dormant against, now closed.
    store = ParquetStore(tmp_path)
    _seed(store)
    snapshot = signal_snapshot_from_store(
        store, D0, index=INDEX, provider=PROVIDER, reference_tenor="3m"
    )

    class _NoData:
        def top_n_members(self, as_of: date) -> tuple[BasketMember, ...]:
            return ()

        def net_dollar_delta(self, legs: object, as_of: date) -> float | None:
            return None

        def forward_unit_dollar_delta(self, as_of: date) -> float | None:
            return None

    config = DispersionConfig(
        index=INDEX,
        top_n=3,
        straddle_tenor="3m",
        entry_threshold=0.55,
        contracts_per_name=2.0,
        delta_band=10.0,
    )
    strategy = DispersionStrategy(config=config, data=_NoData())
    assert strategy.decide_entry(D0, snapshot).action is EntryAction.ENTER
