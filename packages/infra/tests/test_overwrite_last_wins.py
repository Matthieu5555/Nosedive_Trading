"""Contract: the close capture is overwrite-LAST-VALID-wins, gated on a non-empty capture.

The close is a POLL — one validated observation per (instrument, field) per day — so re-firing
``eod_run`` for the same ``trade_date`` must NOT accumulate raw rows (the 2026-06-16 ``casse``: 42
re-fires piled up because the event_id drifted) and must NOT let an empty / closed-market re-fire
wipe a slice already banked. Three behaviours, asserted against a TEMP store (never canonical
``data/``), driving the REAL ``default_stages_builder`` collection stage:

* (1) last-valid-wins — a re-fire with corrected values REPLACES the banked slice (no pile-up,
  latest values win). Blueprint 01-architecture:17 (a re-run is idempotent OR intentionally
  versioned; ``version=`` stays the deliberate-replay hatch, never the routine).
* (2) empty-reject (the safety) — a re-fire carrying ZERO valid two-sided quotes is rejected at
  admission; the banked slice is retained untouched. This is the ``dernier raw qui écrase ne doit
  pas être vide`` guard.
* (3) thin-but-real admit — a basket with FEW (>=1) two-sided quotes is admitted and overwrites;
  the boundary is zero valid quote, NOT "few" (flag-not-reject — the front clamps thin slices).

The oracle is the contract, not the code: raw rows read back from the store after each fire.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from algotrading.infra.actor import IndexBasket
from algotrading.infra.connectivity import ManualClock
from algotrading.infra.contracts import InstrumentMaster, RawMarketEvent
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.orchestration.eod_runner import FiredIndex, default_stages_builder
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import IndexRegistry, parse_index_registry
from fixtures.events import quote_events
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG, make_option, make_underlying

TRADE_DATE = date(2026, 3, 12)
AS_OF = datetime(2026, 3, 12, 16, 30, tzinfo=UTC)
NEXT_OPEN = datetime(2026, 3, 13, 8, 0, tzinfo=UTC)
CLOCK_NOW = datetime(2026, 3, 12, 17, 0, tzinfo=UTC)
_SYMBOL = "SX5E"
_EXPIRY = date(2026, 4, 11)
_RAW = "raw_market_events"


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.5, max_quote_age_seconds=30.0, min_chain_count=1
        ),
        solver=SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200),
        surface=SURFACE_CONFIG,
        forward=FORWARD_CONFIG,
        scenario=ScenarioConfig(version="scn-1", spot_shocks=(-0.05,), vol_shocks=(0.05,)),
    )


def _registry() -> IndexRegistry:
    return parse_index_registry(
        {
            _SYMBOL: {
                "name": "Euro Stoxx 50",
                "calendar": "XETR",
                "currency": "EUR",
                "ibkr": {"conid": 0, "secType": "IND", "exchange": "EUREX"},
                "enabled": True,
            }
        }
    )


def _master(instrument: InstrumentKey) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=TRADE_DATE,
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _basket(
    option_mids: tuple[float, ...], *, two_sided_count: int | None, last_only: bool = False
) -> IndexBasket:
    """A basket for ``SX5E`` with the index underlying plus one option per supplied mid.

    ``last_only`` emits only a ``last`` (no bid/ask) — the closed-market / last-only shape with no
    valid two-sided quote. ``two_sided_count`` is what the collector would report (the gate reads
    it); supplied explicitly so the test pins the boundary without a real broker.
    """
    underlying = make_underlying(_SYMBOL)
    events = list(
        quote_events(
            underlying, bid=99.9, ask=100.1, last=100.0, ts=AS_OF,
            session_id=underlying.canonical(),
        )
    )
    instruments = [underlying]
    masters = [_master(underlying)]
    for index, mid in enumerate(option_mids):
        option = make_option(_SYMBOL, 100.0 + index, "C", _EXPIRY)
        # session_id per instrument (the fixtures' event_id is field-scoped, not instrument-scoped)
        # — keeps the (session_id, event_id) PK unique, mirroring the production capture where the
        # collector's content_event_id carries the instrument identity.
        if last_only:
            events += list(
                quote_events(option, last=mid, ts=AS_OF, session_id=option.canonical())
            )
        else:
            events += list(
                quote_events(
                    option, bid=mid - 0.1, ask=mid + 0.1, last=mid, ts=AS_OF,
                    session_id=option.canonical(),
                )
            )
        instruments.append(option)
        masters.append(_master(option))
    return IndexBasket(
        instruments=tuple(instruments),
        events=tuple(events),
        masters=tuple(masters),
        two_sided_count=two_sided_count,
    )


def _collect(store: ParquetStore, basket: IndexBasket) -> None:
    """Drive one ``eod_run`` collection fire against the store with ``basket`` as the source."""
    source: Callable[[FiredIndex, date], IndexBasket | None] = lambda _f, _d: basket  # noqa: E731
    stages = default_stages_builder(
        store,
        _config(),
        {"cfg": "h"},
        ManualClock(start=CLOCK_NOW),
        "corr-id",
        [FiredIndex(entry=_registry().get(_SYMBOL), as_of=AS_OF, next_open=NEXT_OPEN)],
        basket_source=source,
    )
    stages.collection()


def _raw_asks(store: ParquetStore) -> dict[str, float]:
    """Map each option contract_key -> its banked ASK value (the field we mutate across fires)."""
    rows: list[RawMarketEvent] = store.read(_RAW, trade_date=TRADE_DATE, underlying=_SYMBOL)
    return {
        row.instrument_key: row.value
        for row in rows
        if row.field_name == "ask" and "OPT" in row.instrument_key
    }


def test_re_fire_overwrites_last_valid_wins(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "data")

    _collect(store, _basket((10.0, 20.0), two_sided_count=2))
    first = _raw_asks(store)
    assert len(first) == 2, "first fire banks both options"

    # Re-fire the SAME two contracts with corrected values — the latest valid capture wins and the
    # slot does NOT accumulate (the casse was 42 piled-up re-fires).
    _collect(store, _basket((11.0, 21.0), two_sided_count=2))
    second = _raw_asks(store)
    assert len(second) == 2, "re-fire must REPLACE, not accumulate (no pile-up)"
    assert sorted(round(v, 1) for v in second.values()) == [11.1, 21.1], "latest values win"


def test_empty_re_fire_cannot_overwrite_a_banked_slice(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "data")

    _collect(store, _basket((10.0, 20.0), two_sided_count=2))
    banked = _raw_asks(store)
    assert len(banked) == 2

    # A re-fire with ZERO valid two-sided quotes (closed-market / last-only) must be REJECTED at
    # admission — the banked slice is retained untouched. This is the overwrite safety.
    _collect(store, _basket((0.0, 0.0), two_sided_count=0, last_only=True))
    after = _raw_asks(store)
    assert after == banked, "an empty re-fire must not wipe or alter the banked slice"


def test_thin_but_real_capture_is_admitted_not_dropped(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path / "data")

    _collect(store, _basket((10.0, 20.0, 30.0), two_sided_count=3))
    assert len(_raw_asks(store)) == 3

    # A genuinely thin capture — ONE real two-sided quote — is above the zero boundary, so it is
    # admitted and overwrites (flag-not-reject; the front clamps a degenerate ultra-short slice).
    # The boundary is "zero valid quote", never "few".
    _collect(store, _basket((42.0,), two_sided_count=1))
    thin = _raw_asks(store)
    assert len(thin) == 1, "a thin-but-real capture must PASS (not be dropped) and overwrite"
    assert round(next(iter(thin.values())), 1) == 42.1
