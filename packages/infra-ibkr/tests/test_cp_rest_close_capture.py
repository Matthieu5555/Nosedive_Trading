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
from algotrading.infra_ibkr.collectors.cp_rest_close_capture import collect_live_basket
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
