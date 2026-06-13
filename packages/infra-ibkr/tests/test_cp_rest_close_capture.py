"""``collect_live`` — the real EOD close basket capture over CP REST (WS 1C step 4).

The seam this pins: given an authenticated CP REST transport (a fake gateway here — NO network,
NO secrets) and a fired index, ``collect_live_basket`` resolves the conid, plans/qualifies the
option chain, snapshots the close marks, and returns a populated :class:`IndexBasket` of exactly
the shape :func:`run_analytics` consumes.

The expectations are derived independently of the capture code: the fake gateway lists a known
chain (a fixed set of months × strikes × rights, each with a known conid) and returns known close
marks; the test hand-derives which contracts the capture *must* return and what their close events
must carry, then asserts the basket matches — not by reading back what the code emitted.

The look-ahead obligation has its own tests: a snapshot row updated in ``[close, next_open)`` (the
post-close settlement window) is kept, one updated at/after the next session's open is dropped, and
a capture that keeps zero events after that guard fails loud rather than landing an empty basket.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
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
from algotrading.infra.actor import IndexBasket
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
    collect_live_basket,
)
from algotrading.infra_ibkr.collectors.cp_rest_snapshot import (
    SNAPSHOT_MAX_CONIDS,
    snapshot_index_spot,
    snapshot_with_warmup,
)
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG


def _test_logger() -> object:
    """A structured logger for direct policy-function calls (the capture binds its own)."""
    return structlog.get_logger("test.chain_window")


# The fired index and its own session close (the as-of every captured event is stamped at).
SPX = IndexEntry("SPX", "S&P 500", "XNYS", "USD", IbkrRef(0, "IND", "CBOE"), True)
CLOSE = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)
# The next session's open — the upper bound of the admitted close set. A snapshot row updated in
# [CLOSE, NEXT_OPEN) is the close (post-close settlement marks); one at/after NEXT_OPEN is a later
# session and is dropped. 2026-03-13 is the next NYSE session; its 09:30 ET open is 13:30 UTC (EDT).
NEXT_OPEN = datetime(2026, 3, 13, 13, 30, tzinfo=UTC)
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
        gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
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


def test_capture_keeps_a_post_close_settlement_print() -> None:
    """A row updated AFTER the close but BEFORE the next open is kept — the settlement window.

    This is the regression guard for the SPX miss: the timer fires minutes after the close, so the
    broker's ``_updated`` is already past the close instant — but the row is still the close set.
    One contract's ``_updated`` is moved one minute past the close (well inside ``[CLOSE,
    NEXT_OPEN)``); its events MUST be present, and every event is still stamped at the close.
    """
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
    # The post-close settlement contract DID contribute events (its row was kept).
    assert any(e.instrument_key == settled_key for e in basket.events)
    # Every admitted event is still stamped at the close instant (not the broker update time).
    assert all(e.canonical_ts == CLOSE for e in basket.events)


def test_capture_drops_a_later_session_print() -> None:
    """A snapshot row updated at/after the NEXT session's open is dropped — the look-ahead guard.

    One contract's ``_updated`` is moved to the next session's open (``NEXT_OPEN``): that belongs
    to a later session — a wrong-day catch-up snapshot — so its events must be absent, while every
    admitted event still carries the close instant.
    """
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
    # The later-session contract contributed NO events (its snapshot row was dropped).
    assert all(e.instrument_key != poisoned_key for e in basket.events)
    assert all(e.canonical_ts == CLOSE for e in basket.events)


class _AllLaterSessionGateway(_FakeGateway):
    """Every snapshot row is stamped at the next session's open — a whole wrong-day catch-up.

    The fired index lists options and the snapshot returns them, but every row's ``_updated`` is a
    later session, so the guard drops all of them — the empty-close-set anomaly the loud failure
    is meant to catch (distinct from an index that lists no options at all).
    """

    def _snapshot(self, params: dict[str, Any]) -> Any:
        rows = super()._snapshot(params)
        next_open_ms = int(NEXT_OPEN.timestamp() * 1000)
        for row in rows:
            row["_updated"] = next_open_ms
        return rows


def test_all_rows_in_a_later_session_raises_rather_than_landing_empty() -> None:
    """Contracts came back but every row was dropped → CloseCaptureError, never a silent empty day.

    The loud-failure guard for the silent-miss that started this: a capture that fetches contracts
    but keeps zero events raises (so the runner exits non-zero and ``OnFailure=`` alerts), unlike a
    genuinely optionless index, which returns ``None`` far upstream (see the no-listed-options test).
    """
    with pytest.raises(CloseCaptureError):
        collect_live_basket(
            _AllLaterSessionGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN,
            config=_config(),
            selection=ChainSelection(
                max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE"
            ),
        )


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
        _FakeGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(), selection=selection
    )
    shuffled = collect_live_basket(
        _ShuffledSnapshotGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(), selection=selection
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
    kept = nearest_strikes(ladder, spot=100.0, per_side=16)
    assert kept == [float(strike) for strike in range(84, 116)]  # 32 strikes, 84..115
    assert 116.0 not in kept and 83.0 not in kept  # the distance-16 boundary: lower kept, upper out


def test_nearest_strikes_degrades_for_a_sparse_ladder_and_missing_spot() -> None:
    # Fewer than 2*per_side listed strikes: qualify them all (a sparse name is not over-trimmed).
    assert nearest_strikes({95.0, 100.0, 105.0}, spot=100.0, per_side=16) == [95.0, 100.0, 105.0]
    # No usable spot: centre on the median listed strike (deterministic, just not the true forward).
    # Median of 1..9 is 5; nearest 4 by (|s-5|, s) are {3,4,5,6,7} minus the farther of the ties ->
    # 3,4,5,6 (distance-2 ties 3 and 7 break toward 3).
    assert nearest_strikes({float(s) for s in range(1, 10)}, spot=None, per_side=2) == [
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


def _delta_band_boundary_strike(
    *, forward: float, maturity_years: float, volatility: float, target_call_nd1: float
) -> float:
    """Independent oracle: the strike whose undiscounted call delta ``N(d1)`` equals the target.

    Inverts the normal CDF with scipy ``norm.ppf`` — a *different* path from the pricing engine
    the discovery window reads — so a "contains the band" assertion is genuine agreement, not a
    round-trip. (Same derivation as ``fixtures.synthetic.delta_band_boundary_strike``, inlined so
    this collector test carries its own oracle.)
    """
    from scipy.stats import norm

    d1 = float(norm.ppf(target_call_nd1))
    ln_fk = d1 * volatility * math.sqrt(maturity_years) - 0.5 * volatility**2 * maturity_years
    return forward / math.exp(ln_fk)


def test_discovery_window_is_delta_driven_and_tenor_aware_on_a_dense_ladder() -> None:
    """Discovery qualifies the delta-driven, tenor-aware window — NOT a fixed strike count.

    The T-delta-window fix: the gateway lists 200 strikes per month; the capture must qualify
    the strikes that *contain* the 30Δ band at each tenor (a band whose strike width grows with
    √T), so the longer maturity reaches strictly further out than the nearer one and the window
    extends past the old ±16 (±~1%) block that clipped the band. Asserted on the recorded
    ``/secdef/info`` calls — the qualification itself — with an independent ``norm.ppf`` oracle
    for the band edges.
    """
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

    # Tenor-aware: the longer (SEP) maturity reaches strictly further OTM on BOTH sides than the
    # nearer (JUN) one — a property a fixed strike count cannot have.
    assert max(sep) > max(jun)
    assert min(sep) < min(jun)
    # The clip is gone: the window reaches past the old ±16-strike block ([84, 116] at spot 100).
    assert max(sep) > 116.0 and min(sep) < 84.0

    # Independent oracle: the SEP discovery 20Δ band edges (discovery widens 0.30 → 0.20) at the
    # working vol the capture used (config default 0.40), via scipy norm.ppf. The capture sizes
    # the tenor from the month token's mid-month representative date (day 15), so match that.
    t_sep = (date(2026, 9, 15) - CLOSE.date()).days / 365.0
    low_edge = _delta_band_boundary_strike(
        forward=_SPOT, maturity_years=t_sep, volatility=0.40, target_call_nd1=0.80
    )
    high_edge = _delta_band_boundary_strike(
        forward=_SPOT, maturity_years=t_sep, volatility=0.40, target_call_nd1=0.20
    )
    # Every listed integer strike comfortably inside the band was qualified...
    inside = {float(k) for k in range(1, 201) if low_edge + 1.0 <= k <= high_edge - 1.0}
    assert inside <= sep
    # ...and nothing far outside it was (the window contains the band, it does not balloon).
    assert all(low_edge - 2.0 <= strike <= high_edge + 2.0 for strike in sep)


def test_discovery_runaway_window_fails_loud() -> None:
    """A pathological listing whose band engulfs > the runaway threshold of strikes fails LOUD.

    Full-30Δ has no cap (a cap would be the intent-vs-delivery bound this task removed). The only
    backstop is the runaway valve: a single expiry qualifying an implausible number of strikes
    raises :class:`DiscoveryRunawayError` — it never silently trims. Built with a finely-spaced
    ladder that lies entirely inside the (moderate-vol) delta band, so the qualified count exceeds
    the threshold.
    """
    # 0.05-spaced strikes across [80, 150) — ~1400 points, all inside the 20Δ band at vol 0.40,
    # T≈1y (band ≈ [77.3, 152.0] for forward 100), so the qualified count clears the threshold.
    fine_ladder = {round(80.0 + 0.05 * i, 2) for i in range(1400)}
    assert len(fine_ladder) > DISCOVERY_RUNAWAY_STRIKES_PER_EXPIRY
    strike_selection = StrikeSelectionConfig(
        version="ss-runaway", delta_bound=0.30, min_strikes_per_side=1, discovery_working_vol=0.40
    )
    with pytest.raises(DiscoveryRunawayError):
        qualify_strikes_for_expiry(
            fine_ladder,
            month="MAR27",  # ~1y out from the 2026-03-12 close → a non-degenerate tenor
            spot=100.0,
            as_of=CLOSE.date(),
            strike_selection=strike_selection,
            log=_test_logger(),
        )


def test_discovery_falls_back_to_a_bounded_block_with_no_spot() -> None:
    """With no usable spot there is no forward to delta-bound against → bounded count fallback.

    The delta window cannot be computed without a forward, so discovery degrades to the
    near-the-money count block (centred on the median listed strike) rather than the whole ladder
    — bounded and paced-safe, logged as the degraded path it is.
    """
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
    # The fallback keeps a bounded block, never the full 200-strike ladder.
    assert 0 < len(kept) <= 2 * DISCOVERY_FALLBACK_STRIKES_PER_SIDE
    assert set(kept) < ladder


def test_a_name_with_no_listed_options_is_a_clean_no_capture() -> None:
    """An index that lists no option months returns None (a labeled no-capture), never a crash."""

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
    monkeypatch.setattr(cp_rest_snapshot.time, "sleep", lambda seconds: sleeps.append(seconds))

    basket = collect_live_basket(
        _ColdThenWarmGateway(), index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
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

    spot = snapshot_index_spot(_OneShot(), INDEX_CONID)
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

    rows = snapshot_with_warmup(_BatchRecorder(), conids=requested)

    # Batched 50 / 50 / 20 — no batch exceeds the cap.
    assert [len(b) for b in batches] == [50, 50, 20]
    assert all(len(b) <= SNAPSHOT_MAX_CONIDS for b in batches)
    # Every requested conid was snapshotted exactly once, and all rows came back concatenated.
    flattened = [conid for batch in batches for conid in batch]
    assert sorted(flattened) == requested
    assert {row.conid for row in rows} == set(requested)
