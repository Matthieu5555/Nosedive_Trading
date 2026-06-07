"""Daily close-snapshot capture mode (roadmap WS 1C, Part B).

The close mode reuses the actor's pure path with ``session_open=False`` and an injected
``as_of`` = the index's own ``session_close`` (1J calendar resolver). The named obligations from
the 1C spec's test surface:

* close-snapshot determinism — the same close events twice yield a byte-identical set, and
  reordering the input events leaves the persisted set unchanged (reordering invariance);
* exactly one set per ``(provider, trade_date)`` — a second run replaces, does not duplicate;
* per-index close — a multi-exchange run captures each index at *its own* close instant, never a
  single global close (2026-03-12: NYSE 20:00 UTC, Eurex 21:00 UTC — US DST on, EU not yet);
* no wall clock — the set is a pure function of injected inputs (byte-identical replay);
* the close reference is point-in-time honest (no look-ahead) — covered jointly with the
  reference_spot look-ahead contract in test_close_reference_no_lookahead.py.

Inputs are built with the shared fixture chains, the same way the replay byte-identical test
builds the actor's (events, instruments, masters).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
)
from algotrading.infra.actor import IndexBasket, capture_daily_close, capture_index_close
from algotrading.infra.actor.close_capture import make_close_capture
from algotrading.infra.contracts import InstrumentMaster
from algotrading.infra.contracts.instrument_key import InstrumentKey
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import CalendarResolver
from algotrading.infra.universe.errors import CalendarResolutionError
from algotrading.infra.universe.index_registry import IbkrRef, IndexEntry, IndexRegistry
from fixtures.events import quote_events
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG, get_fixture

# A trade date where the two indices close at DIFFERENT UTC instants: 2026-03-12, US DST already
# started (NYSE 16:00 ET = 20:00 UTC) but EU not yet (Eurex 22:00 CET = 21:00 UTC). This is the
# date that proves per-index close, not a single global close.
TRADE_DATE = date(2026, 3, 12)
CONFIG_HASH = {"cfg": "cfg-hash-close"}

# Hand-resolved expected closes (asserted against the resolver in test_per_index_close_*).
_SPX_CLOSE = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)
_SX5E_CLOSE = datetime(2026, 3, 12, 21, 0, tzinfo=UTC)


def _registry() -> IndexRegistry:
    return IndexRegistry(
        entries=(
            IndexEntry("SPX", "S&P 500", "XNYS", "USD", IbkrRef(1, "IND", "CBOE"), True),
            IndexEntry("SX5E", "EURO STOXX 50", "XEUR", "EUR", IbkrRef(2, "IND", "EUREX"), True),
            # A disabled index must never reach capture.
            IndexEntry("NDX", "Nasdaq 100", "XNYS", "USD", IbkrRef(3, "IND", "NASDAQ"), False),
        )
    )


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", underlyings=("AAPL",), exchange="SMART"),
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


def _master(instrument: InstrumentKey, as_of: datetime) -> InstrumentMaster:
    return InstrumentMaster(
        instrument_key=instrument.canonical(),
        as_of_date=as_of.date(),
        instrument=instrument,
        raw_broker_payload="{}",
    )


def _basket(chain_name: str, as_of: datetime) -> IndexBasket:
    """A close-session basket from a named fixture chain, timestamped at the close instant.

    The close events carry the close instant as their timestamp (the session's close quotes);
    the actor builds the snapshot set with session_open=False over them.
    """
    chain = get_fixture(chain_name)
    spot = chain.underlying_spot
    events = list(
        quote_events(
            chain.underlying, bid=spot - 0.05, ask=spot + 0.05, last=spot, ts=as_of,
            session_id=chain.underlying.canonical(),
        )
    )
    instruments = [chain.underlying]
    masters = [_master(chain.underlying, as_of)]
    for quote in chain.quotes:
        events += list(
            quote_events(
                quote.instrument, bid=quote.bid, ask=quote.ask, last=quote.last, ts=as_of,
                session_id=quote.instrument.canonical(),
            )
        )
        instruments.append(quote.instrument)
        masters.append(_master(quote.instrument, as_of))
    return IndexBasket(
        instruments=tuple(instruments), events=tuple(events), masters=tuple(masters)
    )


# -- per-index close (not a single global close) -----------------------------------------
def test_each_index_is_captured_at_its_own_session_close() -> None:
    resolver = CalendarResolver(_registry())
    # Resolver-resolved closes match the hand-resolved expected UTC instants, and they DIFFER.
    assert resolver.session_close("SPX", TRADE_DATE) == _SPX_CLOSE
    assert resolver.session_close("SX5E", TRADE_DATE) == _SX5E_CLOSE
    assert _SPX_CLOSE != _SX5E_CLOSE

    spx = capture_index_close(
        index=_registry().get("SPX"), basket=_basket("liquid_spy", _SPX_CLOSE),
        resolver=resolver, trade_date=TRADE_DATE, config=_config(), config_hashes=CONFIG_HASH,
    )
    sx5e = capture_index_close(
        index=_registry().get("SX5E"), basket=_basket("liquid_aapl", _SX5E_CLOSE),
        resolver=resolver, trade_date=TRADE_DATE, config=_config(), config_hashes=CONFIG_HASH,
    )
    # Each result is stamped with its own index's close instant.
    assert spx.session_close == _SPX_CLOSE
    assert sx5e.session_close == _SX5E_CLOSE
    # The snapshots carry the index's close as their snapshot_ts (not a shared global close).
    assert {s.snapshot_ts for s in spx.outputs.snapshots} == {_SPX_CLOSE}
    assert {s.snapshot_ts for s in sx5e.outputs.snapshots} == {_SX5E_CLOSE}


# -- determinism: same events twice -> byte-identical set --------------------------------
def test_same_close_events_twice_yield_identical_set() -> None:
    resolver = CalendarResolver(_registry())
    basket = _basket("liquid_spy", _SPX_CLOSE)
    first = capture_index_close(
        index=_registry().get("SPX"), basket=basket, resolver=resolver,
        trade_date=TRADE_DATE, config=_config(), config_hashes=CONFIG_HASH,
    )
    second = capture_index_close(
        index=_registry().get("SPX"), basket=basket, resolver=resolver,
        trade_date=TRADE_DATE, config=_config(), config_hashes=CONFIG_HASH,
    )
    assert first.outputs == second.outputs


def test_reordering_close_events_leaves_the_set_unchanged() -> None:
    resolver = CalendarResolver(_registry())
    basket = _basket("liquid_spy", _SPX_CLOSE)
    reordered = dataclasses.replace(basket, events=tuple(reversed(basket.events)))
    forward = capture_index_close(
        index=_registry().get("SPX"), basket=basket, resolver=resolver,
        trade_date=TRADE_DATE, config=_config(), config_hashes=CONFIG_HASH,
    )
    backward = capture_index_close(
        index=_registry().get("SPX"), basket=reordered, resolver=resolver,
        trade_date=TRADE_DATE, config=_config(), config_hashes=CONFIG_HASH,
    )
    assert forward.outputs == backward.outputs


# -- exactly one set per (provider, trade_date); re-run replaces ---------------------------
def test_rerun_replaces_does_not_duplicate(tmp_path: Path) -> None:
    resolver = CalendarResolver(_registry())
    store = ParquetStore(tmp_path)
    basket = _basket("liquid_spy", _SPX_CLOSE)
    capture_index_close(
        index=_registry().get("SPX"), basket=basket, resolver=resolver, trade_date=TRADE_DATE,
        config=_config(), config_hashes=CONFIG_HASH, store=store,
    )
    first = store.read("market_state_snapshots")
    # Re-run the same day: derived partitions are replaced, not duplicated.
    capture_index_close(
        index=_registry().get("SPX"), basket=basket, resolver=resolver, trade_date=TRADE_DATE,
        config=_config(), config_hashes=CONFIG_HASH, store=store,
    )
    second = store.read("market_state_snapshots")
    assert len(second) == len(first)
    key = lambda s: (s.snapshot_ts, s.instrument_key)  # noqa: E731
    assert sorted(second, key=key) == sorted(first, key=key)


# -- the enabled-set seam: disabled index never captured, no basket -> skipped -------------
def test_capture_daily_close_covers_enabled_indices_only() -> None:
    resolver = CalendarResolver(_registry())
    baskets = {
        "SPX": _basket("liquid_spy", _SPX_CLOSE),
        "SX5E": _basket("liquid_aapl", _SX5E_CLOSE),
        # No NDX basket supplied; NDX is disabled anyway.
    }
    results = capture_daily_close(
        registry=_registry(), baskets=baskets, resolver=resolver, trade_date=TRADE_DATE,
        config=_config(), config_hashes=CONFIG_HASH,
    )
    captured = {r.index for r in results}
    assert captured == {"SPX", "SX5E"}  # canonical order, disabled NDX absent
    assert [r.index for r in results] == ["SPX", "SX5E"]


def test_enabled_index_with_no_basket_is_skipped_not_an_error() -> None:
    resolver = CalendarResolver(_registry())
    # Only SPX has a basket; SX5E enabled but no basket -> skipped, not a crash.
    results = capture_daily_close(
        registry=_registry(), baskets={"SPX": _basket("liquid_spy", _SPX_CLOSE)},
        resolver=resolver, trade_date=TRADE_DATE, config=_config(), config_hashes=CONFIG_HASH,
    )
    assert [r.index for r in results] == ["SPX"]


# -- non-session date raises a labeled error (no guessed instant) -------------------------
def test_capture_on_a_non_session_date_raises_labeled_error() -> None:
    resolver = CalendarResolver(_registry())
    saturday = date(2026, 3, 14)  # a weekend, not a trading session
    with pytest.raises(CalendarResolutionError):
        capture_index_close(
            index=_registry().get("SPX"), basket=_basket("liquid_spy", _SPX_CLOSE),
            resolver=resolver, trade_date=saturday, config=_config(), config_hashes=CONFIG_HASH,
        )


# -- the bound seam for 1G ----------------------------------------------------------------
def test_make_close_capture_binds_into_a_trade_date_callable(tmp_path: Path) -> None:
    resolver = CalendarResolver(_registry())
    store = ParquetStore(tmp_path)
    capture = make_close_capture(
        registry=_registry(), resolver=resolver, config=_config(),
        config_hashes=CONFIG_HASH, store=store,
    )
    results = capture(TRADE_DATE, {"SPX": _basket("liquid_spy", _SPX_CLOSE)})
    assert [r.index for r in results] == ["SPX"]
    assert store.read("market_state_snapshots")  # persisted through the bound store
