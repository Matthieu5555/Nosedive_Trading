"""S3's store-backed data adapter — the as-of I/O path (``StoreBackedGammaData``).

This drives the adapter against a **temporary** ParquetStore (never the canonical ``data/``
store): per-name IV-rank signal rows and a handful of grid cells are written here, then the
adapter resolves the cheapest name, the call's dollar-delta, and the name's spot off them. Every
expected value is computed by hand from the rows written in the test — the cheapest name is the
minimum-IV-rank subject, the call delta is the leg contribution, the share unit is the grid
forward (carry==0 ⇒ spot) — never read back from the code under test. The pure strategy rules
are covered in ``test_s3_gamma.py``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra.contracts import ProjectedOptionAnalytics, StrategySignal
from algotrading.infra.storage import ParquetStore
from algotrading.strategy import GammaConfig, StoreBackedGammaData, gamma_strategy

AS_OF = date(2026, 1, 5)
_TS = datetime(2026, 1, 5, 16, 0, 0, tzinfo=UTC)
PROVIDER = "IBKR"
INDEX = "SX5E"
TENOR = "3m"


def _stamp() -> object:
    return stamp(
        calc_ts=_TS,
        code_version="algotrading-strategy-test",
        config_hashes={"cfg": "cfg"},
        source_records=(source_ref("raw_market_events", "s", "e"),),
        source_timestamps=(_TS,),
    )


def _signal(*, kind: str, subject: str, value: float, tenor: str = TENOR) -> StrategySignal:
    """One persisted signal reading filed under the index book context, about ``subject``."""
    return StrategySignal(
        snapshot_ts=_TS,
        provider=PROVIDER,
        underlying=INDEX,  # book context: the index whose strategy reads this
        signal_kind=kind,
        subject=subject,  # the name the reading is about
        tenor_label=tenor,
        value=value,
        source_snapshot_ts=_TS,
        provenance=_stamp(),
    )


def _grid_row(
    *, underlying: str, delta_band: str, surface_side: str, dollar_delta: float, forward: float
) -> ProjectedOptionAnalytics:
    """One grid cell on the 3m tenor with a chosen ``dollar_delta`` and ``forward`` (=spot)."""
    return ProjectedOptionAnalytics(
        snapshot_ts=_TS,
        provider=PROVIDER,
        underlying=underlying,
        tenor_label=TENOR,
        maturity_years=0.25,
        delta_band=delta_band,
        target_delta=0.0,
        log_moneyness=0.0,
        strike=forward,
        forward_price=forward,
        implied_vol=0.20,
        total_variance=0.20 * 0.20 * 0.25,
        price=4.0,
        delta=0.5,
        gamma=0.02,
        vega=0.31,
        theta=-0.05,
        rho=0.04,
        dollar_delta=dollar_delta,
        dollar_gamma=0.02,
        dollar_vega=0.31,
        dollar_delta_unit="per $1 underlying move",
        dollar_gamma_unit="per 1% underlying move",
        dollar_vega_unit="per 1 vol point",
        model_version="svi-test",
        pricer_version="px-test",
        source_snapshot_ts=_TS,
        provenance=_stamp(),
        dollar_theta=-2.0,
        dollar_rho=1.0,
        dollar_theta_unit="per calendar day",
        dollar_rho_unit="per 1% rate move",
        surface_side=surface_side,
    )


def _seeded_store(tmp_path: Path) -> ParquetStore:
    """A temp store: per-name IV rank (SAP cheapest) + the cheap name's ATM-call grid cell."""
    store = ParquetStore(tmp_path)
    store.write(
        "strategy_signals",
        [
            # SAP (0.18) is cheaper than ASML (0.62) → cheapest_name must pick SAP.
            _signal(kind="iv_rank", subject="ASML", value=0.62),
            _signal(kind="iv_rank", subject="SAP", value=0.18),
            # A different kind and a different tenor that must be filtered out of the IV-rank pick.
            _signal(kind="implied_correlation", subject=INDEX, value=0.05),
            _signal(kind="iv_rank", subject="SAP", value=0.99, tenor="1m"),
        ],
    )
    store.write(
        "projected_option_analytics",
        [
            # SAP's ATM call (the cheap name's long-gamma engine): dollar_delta 30, forward 100.
            _grid_row(
                underlying="SAP", delta_band="atm", surface_side="call",
                dollar_delta=30.0, forward=100.0,
            ),
        ],
    )
    return store


def _config() -> GammaConfig:
    return GammaConfig(
        index=INDEX, option_tenor=TENOR, entry_iv_rank_max=0.30, contracts=2.0, delta_band=10.0
    )


def _data(tmp_path: Path) -> StoreBackedGammaData:
    return StoreBackedGammaData(
        _seeded_store(tmp_path), _config(), reference_tenor=TENOR, provider=PROVIDER
    )


def test_cheapest_name_picks_the_minimum_iv_rank_subject(tmp_path: Path) -> None:
    # SAP 0.18 < ASML 0.62 at the 3m reference tenor → SAP. The 1m row and the non-iv_rank
    # row are filtered out (else the 1m SAP=0.99 or the correlation row could mislead the pick).
    assert _data(tmp_path).cheapest_name(AS_OF) == "SAP"


def test_cheapest_name_is_none_without_iv_rank_rows(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)  # empty — no signals banked
    data = StoreBackedGammaData(store, _config(), reference_tenor=TENOR, provider=PROVIDER)
    assert data.cheapest_name(AS_OF) is None


def test_net_dollar_delta_sums_the_call_legs_grid_contribution(tmp_path: Path) -> None:
    strat = gamma_strategy(
        _seeded_store(tmp_path), _config(), reference_tenor=TENOR, provider=PROVIDER
    )
    call_leg = strat._call_leg("SAP")
    # Hand-derived: the call leg's dollar_delta = quantity (2.0) × row.dollar_delta (30.0) = 60.0.
    assert _data(tmp_path).net_dollar_delta((call_leg,), AS_OF) == pytest.approx(60.0)


def test_share_unit_dollar_delta_is_the_grid_forward(tmp_path: Path) -> None:
    # One long share's dollar delta = spot; carry==0 ⇒ forward==spot ⇒ the ATM-call forward 100.
    assert _data(tmp_path).share_unit_dollar_delta("SAP", AS_OF) == pytest.approx(100.0)


def test_share_unit_dollar_delta_is_none_without_the_call_cell(tmp_path: Path) -> None:
    assert _data(tmp_path).share_unit_dollar_delta("ASML", AS_OF) is None  # no ASML grid cell


def test_construct_sizes_the_stock_hedge_off_the_real_grid_and_signals(tmp_path: Path) -> None:
    strat = gamma_strategy(
        _seeded_store(tmp_path), _config(), reference_tenor=TENOR, provider=PROVIDER
    )
    basket = strat.construct(AS_OF, basket_id="s3-live")
    assert basket.strategy_id == "S3-gamma"
    assert basket.underlying == "SAP"  # the cheapest name, resolved from the signal layer
    # call leg (long 2.0) + stock hedge: shares = -call_delta/spot = -60/100 = -0.6 (short stock).
    assert len(basket.legs) == 2
    call_leg, stock_leg = basket.legs
    assert call_leg.instrument_kind == "option" and call_leg.surface_side == "call"
    assert stock_leg.instrument_kind == "stock"
    assert stock_leg.side == "short" and stock_leg.quantity == pytest.approx(-0.6)
