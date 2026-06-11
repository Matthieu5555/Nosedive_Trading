"""``collect_live`` — the real EOD close basket capture over CP REST (WS 1C step 4).

The seam this pins: given an authenticated CP REST transport (a fake gateway here — NO network,
NO secrets) and a fired index, ``collect_live_basket`` resolves the conid, plans/qualifies the
option chain, snapshots the close marks, and returns a populated :class:`IndexBasket` of exactly
the shape :func:`run_analytics` consumes.

The expectations are derived independently of the capture code: the fake gateway lists a known
chain (a fixed set of months × strikes × rights, each with a known conid) and returns known close
marks; the test hand-derives which contracts the capture *must* return and what their close events
must carry, then asserts the basket matches — not by reading back what the code emitted.

The look-ahead obligation has its own test: a snapshot row stamped *after* the session close is
never folded into the close basket.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from algotrading.core.config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    StrikeSelectionConfig,
    UniverseConfig,
)
from algotrading.infra.actor import IndexBasket
from algotrading.infra.universe import ChainSelection, IbkrRef, IndexEntry
from algotrading.infra_ibkr.collectors import cp_rest_close_capture
from algotrading.infra_ibkr.collectors.cp_rest_close_capture import (
    _DISCOVERY_STRIKES_PER_SIDE,
    _nearest_strikes,
    collect_live_basket,
)
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG

# The fired index and its own session close (the as-of every captured event is stamped at).
SPX = IndexEntry("SPX", "S&P 500", "XNYS", "USD", IbkrRef(0, "IND", "CBOE"), True)
CLOSE = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)
TRADE_DATE = date(2026, 3, 12)
INDEX_CONID = 416904

# A small known chain: two months, three strikes, both rights. Each (expiry, strike, right) has a
# distinct conid and a known close mark. The conids are the gateway's source of truth.
_MONTHS = {"JUN26": date(2026, 6, 19), "SEP26": date(2026, 9, 18)}
_STRIKES = (95.0, 100.0, 105.0)
_SPOT = 100.0


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(
            version="u-1",
            underlyings=("SPX",),
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


def _conid_for(expiry: date, strike: float, right: str) -> int:
    """A deterministic, collision-free conid for one option contract (the gateway's id)."""
    base = 1_000_000 + int(expiry.strftime("%y%m%d")) * 1000
    return base + int(strike) * 2 + (0 if right == "C" else 1)


def _close_mark(strike: float, right: str) -> float:
    """A known close mid for one contract (intrinsic + a fixed premium), the snapshot oracle."""
    intrinsic = max(_SPOT - strike, 0.0) if right == "C" else max(strike - _SPOT, 0.0)
    return intrinsic + 3.0


class _FakeGateway:
    """A fake CP REST gateway: routes search/strikes/info/snapshot by path + params.

    It is the *only* thing faked — the capture logic (conid resolve, plan, capture-key cap,
    snapshot normalize, basket assembly) runs for real over its canned responses. It records no
    secret and opens no socket. ``_updated`` defaults to the close instant; a per-conid override
    lets the look-ahead test stamp one contract after the close.
    """

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
    """The (expiry, strike, right) the capture MUST return — the full listed chain here.

    With min_strikes_per_side=3 and three strikes, the whole ladder is inside the capture window
    on both maturities, so every listed contract is captured. Derived from the gateway's listing,
    independently of the capture code.
    """
    return {
        (expiry, strike, right)
        for expiry in _MONTHS.values()
        for strike in _STRIKES
        for right in ("C", "P")
    }


def _capture() -> IndexBasket | None:
    gateway = _FakeGateway()
    return collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )


def test_collect_live_returns_the_full_qualified_basket() -> None:
    basket = _capture()
    assert basket is not None

    # The index leg plus every listed option contract is in the basket.
    option_keys = {
        (k.expiry, k.strike, k.option_right) for k in basket.instruments if k.is_option()
    }
    assert option_keys == _expected_option_keys()
    # The index underlying is present, carrying the RESOLVED conid (not the registry placeholder 0).
    index_legs = [k for k in basket.instruments if not k.is_option()]
    assert len(index_legs) == 1
    assert index_legs[0].broker_contract_id == str(INDEX_CONID)
    assert index_legs[0].underlying_symbol == "SPX"

    # A master accompanies every instrument, as-of the close date.
    assert len(basket.masters) == len(basket.instruments)
    assert {m.as_of_date for m in basket.masters} == {TRADE_DATE}


def test_close_events_carry_the_session_close_and_the_known_marks() -> None:
    basket = _capture()
    assert basket is not None

    # Every event is stamped at the index's own session close (no wall clock).
    assert {e.canonical_ts for e in basket.events} == {CLOSE}
    assert {e.exchange_ts for e in basket.events} == {CLOSE}
    assert {e.trade_date for e in basket.events} == {TRADE_DATE}

    # The 'last' mark for a known option equals the gateway's close mark (oracle), recovered via
    # the contract's canonical key — derived independently of the capture's normalize path.
    by_key_field = {(e.instrument_key, e.field_name): e.value for e in basket.events}
    sample_expiry = _MONTHS["JUN26"]
    sample = next(
        k for k in basket.instruments
        if k.is_option() and k.expiry == sample_expiry and k.strike == 105.0
        and k.option_right == "C"
    )
    assert by_key_field[(sample.canonical(), "last")] == _close_mark(105.0, "C")


def test_capture_never_admits_a_post_close_print() -> None:
    """A snapshot row stamped AFTER the session close is dropped — the look-ahead guard.

    One contract's ``_updated`` is moved one minute past the close. Its events must be absent
    from the basket; every admitted event still carries the close instant, and no event's source
    timestamp is later than the close.
    """
    poisoned = _conid_for(_MONTHS["JUN26"], 100.0, "C")
    gateway = _FakeGateway(updated_override={poisoned: int(CLOSE.timestamp() * 1000) + 60_000})
    basket = collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is not None

    poisoned_key = next(
        k.canonical() for k in basket.instruments
        if k.is_option() and k.broker_contract_id == str(poisoned)
    )
    # The post-close contract contributed NO events (its snapshot row was dropped).
    assert all(e.instrument_key != poisoned_key for e in basket.events)
    # And no admitted event is stamped after the close.
    assert all(e.canonical_ts <= CLOSE for e in basket.events)


class _ShuffledSnapshotGateway(_FakeGateway):
    """A gateway whose snapshot rows come back in REVERSED order — a re-fire's different ordering.

    Everything else (the chain listing, the marks, the close instants) is identical to
    :class:`_FakeGateway`; only the snapshot row order differs. A retry/re-fire that returns the
    same contracts in a different order must yield the SAME content-addressed event ids, so the
    append-only store dedupes the re-capture rather than keeping a second copy.
    """

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows = super()._snapshot(params)
        return list(reversed(rows))


def test_event_ids_are_invariant_to_snapshot_row_order() -> None:
    # FIX 2: sequence (and therefore event_id) must derive from the contract's stable identity, not
    # the broker's response row order. Re-fire the same capture with the snapshot rows reversed and
    # assert the event-id SET is byte-identical, with no growth in the event count — exactly what an
    # append-only content-addressed store needs to dedupe a retry to a no-op.
    selection = ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE")

    in_order = collect_live_basket(
        _FakeGateway(), index=SPX, as_of=CLOSE, config=_config(), selection=selection
    )
    shuffled = collect_live_basket(
        _ShuffledSnapshotGateway(), index=SPX, as_of=CLOSE, config=_config(), selection=selection
    )
    assert in_order is not None
    assert shuffled is not None

    in_order_ids = {e.event_id for e in in_order.events}
    shuffled_ids = {e.event_id for e in shuffled.events}
    # Same ids -> the re-fire is a content-addressed no-op (no duplicate rows in the raw partition).
    assert shuffled_ids == in_order_ids
    # No phantom growth: both fires produce the same number of events, and ids are unique.
    assert len(shuffled.events) == len(in_order.events)
    assert len(shuffled_ids) == len(shuffled.events)
    # And the per-(instrument, field) value is unchanged across the reorder (the marks are stable).
    by_key_field_in_order = {(e.instrument_key, e.field_name): e.value for e in in_order.events}
    by_key_field_shuffled = {(e.instrument_key, e.field_name): e.value for e in shuffled.events}
    assert by_key_field_shuffled == by_key_field_in_order


def test_nearest_strikes_keeps_the_money_block_around_spot() -> None:
    """``_nearest_strikes`` keeps exactly ``2 * per_side`` strikes nearest spot, lower-tie-first.

    Expected derived independently: from the 1..200 ladder with spot 100 and per_side 16, the 32
    nearest strikes are 100 (distance 0), the 15 symmetric pairs out to ±15 (85..115), and one more
    at distance 16 where 84 and 116 tie — broken toward the lower strike, so 84 is in and 116 is
    out. That is the contiguous block 84..115.
    """
    ladder = {float(strike) for strike in range(1, 201)}
    kept = _nearest_strikes(ladder, spot=100.0, per_side=16)
    assert kept == [float(strike) for strike in range(84, 116)]  # 32 strikes, 84..115
    assert 116.0 not in kept and 83.0 not in kept  # the distance-16 boundary: lower kept, upper out


def test_nearest_strikes_degrades_for_a_sparse_ladder_and_missing_spot() -> None:
    # Fewer than 2*per_side listed strikes: qualify them all (a sparse name is not over-trimmed).
    assert _nearest_strikes({95.0, 100.0, 105.0}, spot=100.0, per_side=16) == [95.0, 100.0, 105.0]
    # No usable spot: centre on the median listed strike (deterministic, just not the true forward).
    # Median of 1..9 is 5; nearest 4 by (|s-5|, s) are {3,4,5,6,7} minus the farther of the ties ->
    # 3,4,5,6 (distance-2 ties 3 and 7 break toward 3).
    assert _nearest_strikes({float(s) for s in range(1, 10)}, spot=None, per_side=2) == [
        3.0, 4.0, 5.0, 6.0,
    ]


class _DenseLadderGateway(_FakeGateway):
    """A gateway whose every expiry lists a dense 1..200 strike ladder (spot 100).

    Mirrors a liquid index (ESTX50 lists ~300 strikes/expiry in fine steps). The capture must
    qualify only the near-the-money block — never one ``/secdef/info`` per listed strike — so the
    paced per-(strike, right) qualification stays bounded regardless of how wide the ladder is.
    """

    _DENSE = tuple(float(strike) for strike in range(1, 201))

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path == "/iserver/secdef/strikes":
            self.calls.append((path, dict(params or {})))
            return {"call": list(self._DENSE), "put": list(self._DENSE)}
        return super().get(path, params)

    def _mark_for_conid(self, conid: int) -> float:
        return 3.0  # a flat mark for any qualified contract — this test pins discovery, not marks


def test_discovery_qualifies_only_the_near_the_money_block_on_a_dense_ladder() -> None:
    """Conid qualification is bounded to ±``_DISCOVERY_STRIKES_PER_SIDE`` strikes per expiry.

    The gateway lists 200 strikes per month; the capture must call ``/secdef/info`` only for the 32
    nearest-the-money strikes (both rights) on each of the two kept maturities, and for no wing
    strike. Asserted on the recorded calls — the qualification cost, not the basket contents.
    """
    gateway = _DenseLadderGateway()
    basket = collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is not None

    info_strikes = {
        float(params["strike"]) for path, params in gateway.calls if path == "/iserver/secdef/info"
    }
    expected_block = {float(strike) for strike in range(84, 116)}  # the 32 nearest to spot 100
    assert info_strikes == expected_block
    # No wing strike was ever qualified (the far ladder is never paid for).
    assert not info_strikes & {1.0, 40.0, 160.0, 200.0}
    # One info call per (strike, right) over both kept months — bounded, not 200-wide.
    info_calls = [path for path, _ in gateway.calls if path == "/iserver/secdef/info"]
    assert len(info_calls) == 2 * (2 * _DISCOVERY_STRIKES_PER_SIDE) * 2  # months × strikes × rights


def test_a_name_with_no_listed_options_is_a_clean_no_capture() -> None:
    """An index that lists no option months returns None (a labeled no-capture), never a crash."""

    class _NoOptionsGateway(_FakeGateway):
        def _search(self) -> Any:
            return [
                {"conid": INDEX_CONID, "symbol": "SPX",
                 "sections": [{"secType": "IND", "exchange": "CBOE"}]}
            ]

    basket = collect_live_basket(
        _NoOptionsGateway(), index=SPX, as_of=CLOSE, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is None


class _ColdThenWarmGateway(_FakeGateway):
    """A gateway that mimics IBKR's cold-snapshot quirk: the first snapshot of any conid set comes
    back metadata-only (no value tags), and only a later poll of the same set returns the marks.

    This is the real failure that drove ``spot=None`` then ``option_count=0`` in production: a
    single un-retried snapshot reads the cold response. The capture must poll the same request
    until the values warm, so over this gateway it still produces the full, marked basket.
    """

    def __init__(self) -> None:
        super().__init__()
        self._warmed: set[str] = set()

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows = super()._snapshot(params)
        conids_key = str(params["conids"])
        if conids_key in self._warmed:
            return rows
        self._warmed.add(conids_key)
        # Cold: strip every value tag, leaving only the conid + a server echo (the real cold shape).
        return [{"conid": row["conid"], "server_id": "q0"} for row in rows]


def test_snapshot_warms_up_before_reading_marks(monkeypatch: Any) -> None:
    """A cold first snapshot must not collapse the capture — the warm-up poll recovers spot + marks.

    Over a gateway whose first snapshot of each conid set is metadata-only, the single-call code
    path saw ``spot=None`` and then selected zero options. With the warm-up poll, the second poll
    returns the marks, so the full chain is centred, captured, and carries its close marks. Sleep
    is stubbed so the test pays no wall-clock cost.
    """
    sleeps: list[float] = []
    monkeypatch.setattr(cp_rest_close_capture.time, "sleep", lambda seconds: sleeps.append(seconds))

    basket = collect_live_basket(
        _ColdThenWarmGateway(), index=SPX, as_of=CLOSE, config=_config(),
        selection=ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"),
    )
    assert basket is not None

    # Spot warmed on the retry, so the whole listed chain was centred and captured — not zero.
    option_keys = {
        (k.expiry, k.strike, k.option_right) for k in basket.instruments if k.is_option()
    }
    assert option_keys == _expected_option_keys()
    # And the warmed marks reached the events (a known contract's 'last' equals the oracle mark).
    by_key_field = {(e.instrument_key, e.field_name): e.value for e in basket.events}
    sample = next(
        k for k in basket.instruments
        if k.is_option() and k.expiry == _MONTHS["JUN26"] and k.strike == 105.0
        and k.option_right == "C"
    )
    assert by_key_field[(sample.canonical(), "last")] == _close_mark(105.0, "C")
    # It polled (warm-up engaged) but did not burn every attempt: one retry per snapshot site
    # (the index spot and the option batch) — bounded, converged early.
    assert len(sleeps) == 2


def test_warm_first_snapshot_does_not_poll() -> None:
    """When the gateway is warm on the first call, the warm-up loop returns at once — no sleep.

    Locks the happy-path cost: a gateway that already carries marks on the first snapshot must not
    incur any retry. Real ``time.sleep`` is left in place; if the loop polled it would block, so a
    fast pass is itself the assertion that no sleep happened.
    """
    rows = [{"conid": INDEX_CONID, "31": "100.0"}]
    calls: list[Any] = []

    class _OneShot:
        def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
            calls.append((path, params))
            return rows

    spot = cp_rest_close_capture._snapshot_index_spot(_OneShot(), INDEX_CONID)
    assert spot == 100.0
    assert len(calls) == 1  # a single snapshot request — no warm-up poll on an already-warm gateway


def test_snapshot_batches_conids_to_stay_under_the_uri_limit() -> None:
    """A large conid set is requested in ``_SNAPSHOT_MAX_CONIDS``-sized batches, not one giant GET.

    A real index chain is hundreds of contracts; one snapshot GET carrying them all overflows the
    gateway URI limit (HTTP 414). The capture must split the request into bounded batches and
    concatenate the rows. Derived independently: 120 conids at a 50-cap is batches of 50, 50, 20,
    and every conid appears exactly once across the requested batches.
    """
    requested = list(range(1000, 1120))  # 120 distinct conids
    batches: list[list[int]] = []

    class _BatchRecorder:
        def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
            conids = [int(text) for text in str((params or {})["conids"]).split(",")]
            batches.append(conids)
            # Warm on the first call: echo each conid back with a value tag so no poll is needed.
            return [{"conid": conid, "31": "1.0"} for conid in conids]

    rows = cp_rest_close_capture._snapshot_with_warmup(_BatchRecorder(), conids=requested)

    # Batched 50 / 50 / 20 — no batch exceeds the cap.
    assert [len(b) for b in batches] == [50, 50, 20]
    assert all(len(b) <= cp_rest_close_capture._SNAPSHOT_MAX_CONIDS for b in batches)
    # Every requested conid was snapshotted exactly once, and all rows came back concatenated.
    flattened = [conid for batch in batches for conid in batch]
    assert sorted(flattened) == requested
    assert {int(row["conid"]) for row in rows} == set(requested)
