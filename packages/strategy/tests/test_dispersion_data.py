from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra.contracts import ProjectedOptionAnalytics
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import MembershipChange, ingest_membership_changes
from algotrading.strategy import (
    DispersionConfig,
    StoreBackedDispersionData,
    dispersion_strategy,
)

AS_OF = date(2026, 1, 5)
_TS = datetime(2026, 1, 5, 16, 0, 0, tzinfo=UTC)
PROVIDER = "IBKR"


def _stamp() -> object:
    return stamp(
        calc_ts=_TS,
        code_version="algotrading-strategy-test",
        config_hashes={"cfg": "cfg"},
        source_records=(source_ref("raw_market_events", "s", "e"),),
        source_timestamps=(_TS,),
    )


def _row(
    *, underlying: str, delta_band: str, surface_side: str, dollar_delta: float
) -> ProjectedOptionAnalytics:
    return ProjectedOptionAnalytics(
        snapshot_ts=_TS,
        provider=PROVIDER,
        underlying=underlying,
        tenor_label="3m",
        maturity_years=0.25,
        delta_band=delta_band,
        target_delta=0.0,
        log_moneyness=0.0,
        strike=100.0,
        forward_price=100.0,
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
    store = ParquetStore(tmp_path)
    ingest_membership_changes(
        store,
        (
            MembershipChange("SX5E", "ASML", date(2024, 1, 1), None, date(2024, 1, 1),
                             "test", 0.6),
            MembershipChange("SX5E", "SAP", date(2024, 1, 1), None, date(2024, 1, 1),
                             "test", 0.4),
        ),
    )
    store.write(
        "projected_option_analytics",
        [
            _row(underlying="ASML", delta_band="atm", surface_side="call", dollar_delta=60.0),
            _row(underlying="ASML", delta_band="atmp", surface_side="put", dollar_delta=-40.0),
            _row(underlying="SAP", delta_band="atm", surface_side="call", dollar_delta=60.0),
            _row(underlying="SAP", delta_band="atmp", surface_side="put", dollar_delta=-40.0),
            _row(underlying="SX5E", delta_band="atm", surface_side="combined", dollar_delta=50.0),
            _row(underlying="SX5E", delta_band="atmp", surface_side="combined",
                 dollar_delta=-50.0),
        ],
    )
    return store


def _config() -> DispersionConfig:
    return DispersionConfig(
        index="SX5E", top_n=2, straddle_tenor="3m", entry_threshold=0.55, contracts_per_name=2.0
    )


def test_top_n_members_resolves_ranked_basket(tmp_path: Path) -> None:
    data = StoreBackedDispersionData(_seeded_store(tmp_path), _config(), provider=PROVIDER)
    members = data.top_n_members(AS_OF)
    assert [m.constituent for m in members] == ["ASML", "SAP"]


def test_net_dollar_delta_sums_each_legs_grid_contribution(tmp_path: Path) -> None:
    data = StoreBackedDispersionData(_seeded_store(tmp_path), _config(), provider=PROVIDER)
    strat = dispersion_strategy(_seeded_store(tmp_path), _config(), provider=PROVIDER)
    straddle_legs = strat._straddle_legs(data.top_n_members(AS_OF))
    assert data.net_dollar_delta(straddle_legs, AS_OF) == pytest.approx(80.0)


def test_forward_unit_dollar_delta_is_short_call_long_put_on_index(tmp_path: Path) -> None:
    data = StoreBackedDispersionData(_seeded_store(tmp_path), _config(), provider=PROVIDER)
    assert data.forward_unit_dollar_delta(AS_OF) == pytest.approx(-100.0)


def test_construct_sizes_the_forward_off_the_real_grid(tmp_path: Path) -> None:
    strat = dispersion_strategy(_seeded_store(tmp_path), _config(), provider=PROVIDER)
    basket = strat.construct(AS_OF, basket_id="s1-live")
    assert len(basket.legs) == 6
    assert basket.strategy_id == "S1-dispersion"
    call_leg, put_leg = basket.legs[4], basket.legs[5]
    assert call_leg.underlying == "SX5E" and call_leg.side == "short"
    assert call_leg.quantity == pytest.approx(-0.8)
    assert put_leg.side == "long" and put_leg.quantity == pytest.approx(0.8)


def test_missing_grid_rows_make_net_delta_unresolvable(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ingest_membership_changes(
        store,
        (MembershipChange("SX5E", "ASML", date(2024, 1, 1), None, date(2024, 1, 1), "t", 1.0),),
    )
    data = StoreBackedDispersionData(store, _config(), provider=PROVIDER)
    strat = dispersion_strategy(store, _config(), provider=PROVIDER)
    straddle_legs = strat._straddle_legs(data.top_n_members(AS_OF))
    assert data.net_dollar_delta(straddle_legs, AS_OF) is None
