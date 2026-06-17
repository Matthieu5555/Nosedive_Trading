from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
import structlog
from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    StrikeSelectionConfig,
    UniverseConfig,
)
from algotrading.infra.actor import IndexBasket, run_analytics
from algotrading.infra.universe import ChainSelection, IbkrRef, IndexEntry
from algotrading.infra_ibkr.collectors import cp_rest_snapshot
from algotrading.infra_ibkr.collectors.cp_rest_chain_window import (
    DISCOVERY_FALLBACK_STRIKES_PER_SIDE,
    DISCOVERY_RUNAWAY_STRIKES_PER_EXPIRY,
    nearest_strikes,
    qualify_strikes_for_expiry,
)
from algotrading.infra_ibkr.collectors.cp_rest_close_capture import (
    CloseCaptureError,
    DiscoveryRunawayError,
    _discover_chain,
    _selection_from_config,
    collect_live_basket,
    target_from_index,
)
from algotrading.infra_ibkr.collectors.cp_rest_discovery import CpRestDiscovery
from algotrading.infra_ibkr.collectors.cp_rest_snapshot import (
    SNAPSHOT_MAX_CONIDS,
    snapshot_index_spot,
    snapshot_with_warmup,
)
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG


def _test_logger() -> object:
    return structlog.get_logger("test.chain_window")


SPX = IndexEntry("SPX", "S&P 500", "XNYS", "USD", IbkrRef(0, "IND", "CBOE"), True)
CLOSE = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)
NEXT_OPEN = datetime(2026, 3, 13, 13, 30, tzinfo=UTC)
TRADE_DATE = date(2026, 3, 12)
INDEX_CONID = 416904

_MONTHS = {"JUN26": date(2026, 6, 19), "SEP26": date(2026, 9, 18)}
_STRIKES = (95.0, 100.0, 105.0)
_SPOT = 100.0


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(
            version="u-1",
            exchange="CBOE",
            tenor_grid=("1m", "3m"),
            strike_selection=StrikeSelectionConfig(version="ss-1", min_strikes_per_side=3),
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


def test_selection_from_config_keeps_every_listed_maturity() -> None:
    selection = _selection_from_config(_config())
    assert selection.max_expiries is None
    assert not selection.targets_tenors
    assert selection.min_strikes_per_side == 3
    assert selection.option_exchange == "CBOE"


def _conid_for(expiry: date, strike: float, right: str) -> int:
    base = 1_000_000 + int(expiry.strftime("%y%m%d")) * 1000
    return base + int(strike) * 2 + (0 if right == "C" else 1)


def _close_mark(strike: float, right: str) -> float:
    intrinsic = max(_SPOT - strike, 0.0) if right == "C" else max(strike - _SPOT, 0.0)
    return intrinsic + 3.0


class _FakeGateway:

    def __init__(self, *, updated_override: dict[int, int] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._updated_override = updated_override or {}
        self._close_ms = int(CLOSE.timestamp() * 1000)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        params = dict(params or {})
        self.calls.append((path, params))
        if path == "/iserver/secdef/search":
            return self._search()
        if path == "/iserver/secdef/strikes":
            return {"call": list(_STRIKES), "put": list(_STRIKES)}
        if path == "/iserver/secdef/info":
            return self._info(params)
        if path == "/iserver/marketdata/snapshot":
            return self._snapshot(params)
        raise AssertionError(f"unexpected path {path!r}")

    def _search(self) -> Any:
        return [
            {
                "conid": INDEX_CONID,
                "symbol": "SPX",
                "sections": [
                    {"secType": "IND", "exchange": "CBOE"},
                    {"secType": "OPT", "months": ";".join(_MONTHS), "exchange": "CBOE"},
                ],
            }
        ]

    def _info(self, params: dict[str, Any]) -> Any:
        month = params["month"]
        strike = float(params["strike"])
        right = str(params["right"])
        expiry = _MONTHS[month]
        return [
            {
                "conid": str(_conid_for(expiry, strike, right)),
                "maturityDate": expiry.strftime("%Y%m%d"),
                "strike": str(strike),
                "right": right,
            }
        ]

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows: list[dict[str, Any]] = []
        for conid_text in str(params["conids"]).split(","):
            conid = int(conid_text)
            updated = self._updated_override.get(conid, self._close_ms)
            if conid == INDEX_CONID:
                rows.append({"conid": conid, "31": str(_SPOT), "_updated": updated})
                continue
            mark = self._mark_for_conid(conid)
            rows.append(
                {"conid": conid, "31": f"{mark:.2f}", "84": f"{mark - 0.1:.2f}",
                 "86": f"{mark + 0.1:.2f}", "_updated": updated}
            )
        return rows

    def _mark_for_conid(self, conid: int) -> float:
        for expiry in _MONTHS.values():
            for strike in _STRIKES:
                for right in ("C", "P"):
                    if _conid_for(expiry, strike, right) == conid:
                        return _close_mark(strike, right)
        raise AssertionError(f"no mark for conid {conid}")


def _expected_option_keys() -> set[tuple[date, float, str]]:
    return {
        (expiry, strike, right)
        for expiry in _MONTHS.values()
        for strike in _STRIKES
        for right in ("C", "P")
    }


def _capture() -> IndexBasket | None:
    gateway = _FakeGateway()
    return collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )


def test_collect_live_returns_the_full_qualified_basket() -> None:
    basket = _capture()
    assert basket is not None

    option_keys = {
        (k.expiry, k.strike, k.option_right) for k in basket.instruments if k.is_option()
    }
    assert option_keys == _expected_option_keys()
    index_legs = [k for k in basket.instruments if not k.is_option()]
    assert len(index_legs) == 1
    assert index_legs[0].broker_contract_id == str(INDEX_CONID)
    assert index_legs[0].underlying_symbol == "SPX"

    assert len(basket.masters) == len(basket.instruments)
    assert {m.as_of_date for m in basket.masters} == {TRADE_DATE}


def test_close_events_carry_the_session_close_and_the_known_marks() -> None:
    basket = _capture()
    assert basket is not None

    assert {e.canonical_ts for e in basket.events} == {CLOSE}
    assert {e.exchange_ts for e in basket.events} == {CLOSE}
    assert {e.trade_date for e in basket.events} == {TRADE_DATE}

    by_key_field = {(e.instrument_key, e.field_name): e.value for e in basket.events}
    sample_expiry = _MONTHS["JUN26"]
    sample = next(
        k for k in basket.instruments
        if k.is_option() and k.expiry == sample_expiry and k.strike == 105.0
        and k.option_right == "C"
    )
    assert by_key_field[(sample.canonical(), "last")] == _close_mark(105.0, "C")


def test_capture_keeps_a_post_close_settlement_print() -> None:
    settled = _conid_for(_MONTHS["JUN26"], 100.0, "C")
    gateway = _FakeGateway(updated_override={settled: int(CLOSE.timestamp() * 1000) + 60_000})
    basket = collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is not None

    settled_key = next(
        k.canonical() for k in basket.instruments
        if k.is_option() and k.broker_contract_id == str(settled)
    )
    assert any(e.instrument_key == settled_key for e in basket.events)
    assert all(e.canonical_ts == CLOSE for e in basket.events)


def test_broker_update_time_is_preserved_as_a_distinct_exchange_ts() -> None:
    """The three timestamps stay distinct (blueprint 01-architecture §60): the broker's real update
    time is preserved in ``exchange_ts`` while ``canonical_ts``/``receipt_ts`` are the normalized close.

    One contract's ``_updated`` is moved one minute past the close (the settlement window). Its
    events must carry ``exchange_ts`` = that real broker instant — NOT collapsed to the close —
    while ``canonical_ts`` (the ordering / as-of clock) and ``receipt_ts`` stay at the close, so the
    broker observation time is auditable yet the close ordering (and thus the derived analytics) is
    unchanged. A contract WITHOUT an override reports the close as its own update time, so its three
    timestamps coincide — that is correct (the broker time IS the close for it), not a collapse.
    """
    broker_ts = CLOSE + timedelta(minutes=1)  # independent oracle for the overridden _updated
    settled = _conid_for(_MONTHS["JUN26"], 100.0, "C")
    gateway = _FakeGateway(updated_override={settled: int(broker_ts.timestamp() * 1000)})
    basket = collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is not None
    settled_key = next(
        k.canonical() for k in basket.instruments
        if k.is_option() and k.broker_contract_id == str(settled)
    )
    settled_events = [e for e in basket.events if e.instrument_key == settled_key]
    assert settled_events, "the settlement-window contract must contribute events"
    for e in settled_events:
        assert e.exchange_ts == broker_ts  # the real broker update time, preserved (not discarded)
        assert e.canonical_ts == CLOSE  # the normalized close ordering / as-of clock
        assert e.receipt_ts == CLOSE  # when we captured the close
        assert e.exchange_ts != e.canonical_ts  # the distinction the blueprint mandates
    # A non-overridden contract reports the close as its update time → its three timestamps coincide.
    other_events = [e for e in basket.events if e.instrument_key != settled_key]
    assert other_events and all(e.exchange_ts == CLOSE for e in other_events)


def test_capture_drops_a_later_session_print() -> None:
    poisoned = _conid_for(_MONTHS["JUN26"], 100.0, "C")
    gateway = _FakeGateway(updated_override={poisoned: int(NEXT_OPEN.timestamp() * 1000)})
    basket = collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is not None

    poisoned_key = next(
        k.canonical() for k in basket.instruments
        if k.is_option() and k.broker_contract_id == str(poisoned)
    )
    assert all(e.instrument_key != poisoned_key for e in basket.events)
    assert all(e.canonical_ts == CLOSE for e in basket.events)


class _AllLaterSessionGateway(_FakeGateway):

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows = super()._snapshot(params)
        next_open_ms = int(NEXT_OPEN.timestamp() * 1000)
        for row in rows:
            row["_updated"] = next_open_ms
        return rows


def test_all_rows_in_a_later_session_raises_rather_than_landing_empty() -> None:
    with pytest.raises(CloseCaptureError):
        collect_live_basket(
            _AllLaterSessionGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN,
            config=_config(),
            selection=ChainSelection(
                max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"
            ),
        )


class _ShuffledSnapshotGateway(_FakeGateway):

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows = super()._snapshot(params)
        return list(reversed(rows))


def test_event_ids_are_invariant_to_snapshot_row_order() -> None:
    selection = ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE")

    in_order = collect_live_basket(
        _FakeGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(), selection=selection
    )
    shuffled = collect_live_basket(
        _ShuffledSnapshotGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(), selection=selection
    )
    assert in_order is not None
    assert shuffled is not None

    in_order_ids = {e.event_id for e in in_order.events}
    shuffled_ids = {e.event_id for e in shuffled.events}
    assert shuffled_ids == in_order_ids
    assert len(shuffled.events) == len(in_order.events)
    assert len(shuffled_ids) == len(shuffled.events)
    by_key_field_in_order = {(e.instrument_key, e.field_name): e.value for e in in_order.events}
    by_key_field_shuffled = {(e.instrument_key, e.field_name): e.value for e in shuffled.events}
    assert by_key_field_shuffled == by_key_field_in_order


def test_nearest_strikes_keeps_the_money_block_around_spot() -> None:
    ladder = {float(strike) for strike in range(1, 201)}
    kept = nearest_strikes(ladder, spot=100.0, per_side=16)
    assert kept == [float(strike) for strike in range(84, 116)]
    assert 116.0 not in kept and 83.0 not in kept


def test_nearest_strikes_degrades_for_a_sparse_ladder_and_missing_spot() -> None:
    assert nearest_strikes({95.0, 100.0, 105.0}, spot=100.0, per_side=16) == [95.0, 100.0, 105.0]
    assert nearest_strikes({float(s) for s in range(1, 10)}, spot=None, per_side=2) == [
        3.0, 4.0, 5.0, 6.0,
    ]


class _DenseLadderGateway(_FakeGateway):

    _DENSE = tuple(float(strike) for strike in range(1, 201))

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/iserver/secdef/strikes":
            self.calls.append((path, dict(params or {})))
            return {"call": list(self._DENSE), "put": list(self._DENSE)}
        return super().get(path, params)

    def _mark_for_conid(self, conid: int) -> float:
        return 3.0


def _delta_band_boundary_strike(
    *, forward: float, maturity_years: float, volatility: float, target_call_nd1: float
) -> float:
    from scipy.stats import norm

    d1 = float(norm.ppf(target_call_nd1))
    ln_fk = d1 * volatility * math.sqrt(maturity_years) - 0.5 * volatility**2 * maturity_years
    return forward / math.exp(ln_fk)


def test_discovery_window_is_delta_driven_and_tenor_aware_on_a_dense_ladder() -> None:
    gateway = _DenseLadderGateway()
    basket = collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is not None

    info_by_month: dict[str, set[float]] = {}
    for path, params in gateway.calls:
        if path == "/iserver/secdef/info":
            info_by_month.setdefault(str(params["month"]), set()).add(float(params["strike"]))
    jun, sep = info_by_month["JUN26"], info_by_month["SEP26"]

    assert max(sep) > max(jun)
    assert min(sep) < min(jun)
    assert max(sep) > 116.0 and min(sep) < 84.0

    t_sep = (date(2026, 9, 15) - CLOSE.date()).days / 365.0
    low_edge = _delta_band_boundary_strike(
        forward=_SPOT, maturity_years=t_sep, volatility=0.40, target_call_nd1=0.80
    )
    high_edge = _delta_band_boundary_strike(
        forward=_SPOT, maturity_years=t_sep, volatility=0.40, target_call_nd1=0.20
    )
    inside = {float(k) for k in range(1, 201) if low_edge + 1.0 <= k <= high_edge - 1.0}
    assert inside <= sep
    assert all(low_edge - 2.0 <= strike <= high_edge + 2.0 for strike in sep)


def test_discovery_runaway_window_fails_loud() -> None:
    fine_ladder = {round(80.0 + 0.05 * i, 2) for i in range(1400)}
    assert len(fine_ladder) > DISCOVERY_RUNAWAY_STRIKES_PER_EXPIRY
    strike_selection = StrikeSelectionConfig(
        version="ss-runaway", delta_bound=0.30, min_strikes_per_side=1, discovery_working_vol=0.40
    )
    with pytest.raises(DiscoveryRunawayError):
        qualify_strikes_for_expiry(
            fine_ladder,
            month="MAR27",
            spot=100.0,
            as_of=CLOSE.date(),
            strike_selection=strike_selection,
            log=_test_logger(),
        )


def test_discovery_falls_back_to_a_bounded_block_with_no_spot() -> None:
    ladder = {float(k) for k in range(1, 201)}
    strike_selection = StrikeSelectionConfig(
        version="ss-nospot", delta_bound=0.30, min_strikes_per_side=1, discovery_working_vol=0.40
    )
    kept = qualify_strikes_for_expiry(
        ladder,
        month="SEP26",
        spot=None,
        as_of=CLOSE.date(),
        strike_selection=strike_selection,
        log=_test_logger(),
    )
    assert 0 < len(kept) <= 2 * DISCOVERY_FALLBACK_STRIKES_PER_SIDE
    assert set(kept) < ladder


def test_a_name_with_no_listed_options_is_a_clean_no_capture() -> None:

    class _NoOptionsGateway(_FakeGateway):
        def _search(self) -> Any:
            return [
                {"conid": INDEX_CONID, "symbol": "SPX",
                 "sections": [{"secType": "IND", "exchange": "CBOE"}]}
            ]

    basket = collect_live_basket(
        _NoOptionsGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is None


class _ColdThenWarmGateway(_FakeGateway):

    def __init__(self) -> None:
        super().__init__()
        self._warmed: set[str] = set()

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows = super()._snapshot(params)
        conids_key = str(params["conids"])
        if conids_key in self._warmed:
            return rows
        self._warmed.add(conids_key)
        return [{"conid": row["conid"], "server_id": "q0"} for row in rows]


def test_snapshot_warms_up_before_reading_marks(monkeypatch: Any) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(cp_rest_snapshot.time, "sleep", lambda seconds: sleeps.append(seconds))

    basket = collect_live_basket(
        _ColdThenWarmGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is not None

    option_keys = {
        (k.expiry, k.strike, k.option_right) for k in basket.instruments if k.is_option()
    }
    assert option_keys == _expected_option_keys()
    by_key_field = {(e.instrument_key, e.field_name): e.value for e in basket.events}
    sample = next(
        k for k in basket.instruments
        if k.is_option() and k.expiry == _MONTHS["JUN26"] and k.strike == 105.0
        and k.option_right == "C"
    )
    assert by_key_field[(sample.canonical(), "last")] == _close_mark(105.0, "C")
    assert len(sleeps) == 2


def test_warm_first_snapshot_does_not_poll() -> None:
    rows = [{"conid": INDEX_CONID, "31": "100.0"}]
    calls: list[Any] = []

    class _OneShot:
        def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
            calls.append((path, params))
            return rows

    spot = snapshot_index_spot(_OneShot(), INDEX_CONID)
    assert spot == 100.0
    assert len(calls) == 1


def test_snapshot_batches_conids_to_stay_under_the_uri_limit() -> None:
    requested = list(range(1000, 1120))
    batches: list[list[int]] = []

    class _BatchRecorder:
        def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
            conids = [int(text) for text in str((params or {})["conids"]).split(",")]
            batches.append(conids)
            return [{"conid": conid, "31": "1.0"} for conid in conids]

    rows = snapshot_with_warmup(_BatchRecorder(), conids=requested)

    assert [len(b) for b in batches] == [50, 50, 20]
    assert all(len(b) <= SNAPSHOT_MAX_CONIDS for b in batches)
    flattened = [conid for batch in batches for conid in batch]
    assert sorted(flattened) == requested
    assert {row.conid for row in rows} == set(requested)


def _discover_with_pool(pool_size: int) -> tuple[Any, dict[str, str], int]:
    gateway = _FakeGateway()
    discovery = CpRestDiscovery(gateway, exchange="CBOE", currency="USD")
    strike_selection = StrikeSelectionConfig(
        version="ss-pool", min_strikes_per_side=3, discovery_pool_size=pool_size
    )
    chain, conid_by_contract, _month_by_token = _discover_chain(
        discovery,
        target=target_from_index(SPX),
        conid=INDEX_CONID,
        months=list(_MONTHS),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
        spot=_SPOT,
        as_of=TRADE_DATE,
        strike_selection=strike_selection,
    )
    info_calls = sum(1 for path, _ in gateway.calls if path == "/iserver/secdef/info")
    return chain, conid_by_contract, info_calls


def test_concurrent_discovery_is_byte_identical_to_sequential() -> None:
    seq_chain, seq_conids, seq_calls = _discover_with_pool(1)
    par_chain, par_conids, par_calls = _discover_with_pool(6)

    assert par_chain == seq_chain
    assert par_conids == seq_conids
    assert par_calls == seq_calls
    assert seq_calls == len(_MONTHS) * len(_STRIKES) * 2
    assert len(seq_conids) == len(_MONTHS) * len(_STRIKES) * 2


def test_discovery_pool_size_is_clamped_to_at_least_one() -> None:
    chain_1, conids_1, _ = _discover_with_pool(1)
    chain_n, conids_n, _ = _discover_with_pool(64)
    assert chain_1 == chain_n
    assert conids_1 == conids_n


class _ClosedMarketGateway(_FakeGateway):

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows: list[dict[str, Any]] = []
        for conid_text in str(params["conids"]).split(","):
            conid = int(conid_text)
            if conid == INDEX_CONID:
                rows.append({"conid": conid, "31": str(_SPOT), "_updated": self._close_ms})
                continue
            mark = self._mark_for_conid(conid)
            rows.append(
                {"conid": conid, "31": f"{mark:.2f}", "84": "-1", "86": "-1",
                 "_updated": self._close_ms}
            )
        return rows


def test_closed_market_basket_is_captured_to_raw_faithfully_not_dropped() -> None:
    basket = collect_live_basket(
        _ClosedMarketGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is not None, "a closed-market basket must still be captured to raw, not dropped"
    # Every listed option's last-only observation is recorded (the marks land in raw, not erased).
    option_keys = {k.canonical() for k in basket.instruments if k.is_option()}
    event_keys = {e.instrument_key for e in basket.events}
    assert option_keys <= event_keys, "every closed-market option row must contribute raw events"


def test_closed_market_basket_through_analytics_persists_snapshots_but_no_iv() -> None:
    """End-to-end on the REAL ``-1`` wire path: capture → ``run_analytics``.

    The closed-market basket (bid/ask = the ``-1`` sentinel → no two-sided quote, only ``last``
    real) is captured faithfully, then driven through the same analytics choke the live and replay
    paths share. The observations PERSIST as (last-fallback) snapshots — faithful raw — but produce
    ZERO IV points and an EMPTY surface grid: the derived two-sided gate
    (``actor.driver._has_two_sided_option_quote``) admits no option to the solver. That empty grid
    is exactly what the QC coverage-floor checks page on (→ non-zero exit → OnFailure alert), so the
    canary fails LOUD instead of banking a converged surface off ``last``-only marks or silently
    exiting 0. Closes the full real-wire seam (sentinel → normalize → capture → analytics).
    """
    basket = collect_live_basket(
        _ClosedMarketGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is not None
    outputs = run_analytics(
        basket.events, [], instruments=basket.instruments, masters=basket.masters,
        config=_config(), config_hashes={"cfg": "closed-market"}, as_of=CLOSE, calc_ts=CLOSE,
    )
    option_keys = {k.canonical() for k in basket.instruments if k.is_option()}
    snapshot_keys = {s.instrument_key for s in outputs.snapshots}
    # FAITHFUL: the closed-market option observations persisted as (last-fallback) snapshots.
    assert option_keys & snapshot_keys, "closed-market options must persist as flagged snapshots"
    # DERIVED GATE held end-to-end: no option priced, and the surface grid is empty (QC pages).
    assert not outputs.iv_points, "a closed-market basket must produce no IV points"
    assert not outputs.surface_grid, "a closed-market basket must produce an empty surface grid"


def test_genuine_two_sided_close_passes_untouched() -> None:
    basket = _capture()
    assert basket is not None
    option_keys = {
        (k.expiry, k.strike, k.option_right) for k in basket.instruments if k.is_option()
    }
    assert option_keys == _expected_option_keys()


class _OneSidedGateway(_FakeGateway):

    _QUARANTINED = (_MONTHS["JUN26"], 105.0, "C")

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows = super()._snapshot(params)
        bad_conid = _conid_for(*self._QUARANTINED)
        for row in rows:
            if int(row["conid"]) == bad_conid:
                row["84"] = "-1"
        return rows


def test_single_sided_row_is_captured_to_raw_faithfully_and_basket_still_banks() -> None:
    basket = collect_live_basket(
        _OneSidedGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is not None
    quarantined = _conid_for(*_OneSidedGateway._QUARANTINED)
    quarantined_key = next(
        k.canonical() for k in basket.instruments
        if k.is_option() and k.broker_contract_id == str(quarantined)
    )
    assert any(e.instrument_key == quarantined_key for e in basket.events)
    all_option_keys = {
        k.canonical() for k in basket.instruments if k.is_option()
    }
    event_keys = {e.instrument_key for e in basket.events}
    assert all_option_keys <= event_keys
