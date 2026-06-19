from __future__ import annotations

import math
from datetime import UTC, date, datetime

import pytest
from algotrading.core.config import MonetizationConfig
from algotrading.core.provenance import source_ref, stamp
from algotrading.infra.contracts import IvDiagnostics, IvPoint
from algotrading.infra.surfaces import (
    PINNED_TENORS,
    SliceFit,
    SnapshotMarketState,
    fit_slice,
    project_grid,
    tenor_years,
)
from algotrading.infra.surfaces.projection import ProjectionConfig
from fixtures.library import SURFACE_CONFIG
from fixtures.synthetic import SyntheticTermSurface, build_synthetic_term_surface

_TS = datetime(2026, 6, 15, 15, 30, tzinfo=UTC)
_EXPIRY = date(2026, 9, 15)
_CONFIG_HASHES = {"universe": "u", "pricing": "p"}


def _iv_points_for_slice(surface: object, underlying: str) -> tuple[IvPoint, ...]:
    points = []
    for p in surface.points:  # type: ignore[attr-defined]
        key = f"{underlying}|OPT|C|{surface.maturity_years:.4f}|{p.strike:g}"  # type: ignore[attr-defined]
        a_stamp = stamp(
            calc_ts=_TS, code_version="iv-1", config_hashes={"cfg": "c"},
            source_records=(source_ref("market_state_snapshots", _TS, key),),
            source_timestamps=(_TS,),
        )
        points.append(IvPoint(
            snapshot_ts=_TS, contract_key=key,
            implied_vol=p.sigma, log_moneyness=p.log_moneyness,
            total_variance=p.total_variance, solver_version="iv-1",
            diagnostics=IvDiagnostics(converged=True, iterations=5, residual=1e-12,
                                      status="converged"),
            source_snapshot_ts=_TS, provenance=a_stamp,
        ))
    return tuple(points)


def _fit_term_surface(term: SyntheticTermSurface) -> tuple[SliceFit, ...]:
    return tuple(
        fit_slice(
            "SX5E", s.maturity_years, _iv_points_for_slice(s, "SX5E"),
            expiry_date=_EXPIRY, day_count="ACT/365", config=SURFACE_CONFIG,
        )
        for s in term.slices
    )


def _market(term: SyntheticTermSurface) -> SnapshotMarketState:
    return SnapshotMarketState(
        underlying="SX5E", provider="IBKR", spot=term.forward,
        discount_factors={
            tenor_years(t): math.exp(-term.rate * tenor_years(t)) for t in PINNED_TENORS
        },
        default_discount_factor=1.0,
        spot_is_forward=True,
    )


def _project(term: SyntheticTermSurface) -> object:
    slices = _fit_term_surface(term)
    return project_grid(
        slices, _market(term),
        snapshot_ts=_TS, source_snapshot_ts=_TS, calc_ts=_TS,
        projection=ProjectionConfig(version="mirror-test"),
        monetization=MonetizationConfig(version="mon-test"),
        config_hashes=_CONFIG_HASHES,
    )


def test_mirror_fields_present_on_every_cell() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    for cell in result.cells:  # type: ignore[attr-defined]
        assert cell.price_mirror is not None, f"price_mirror missing on {cell.tenor_label}/{cell.delta_band}"
        assert cell.delta_mirror is not None, f"delta_mirror missing on {cell.tenor_label}/{cell.delta_band}"
        assert cell.theta_mirror is not None, f"theta_mirror missing on {cell.tenor_label}/{cell.delta_band}"
        assert cell.rho_mirror is not None, f"rho_mirror missing on {cell.tenor_label}/{cell.delta_band}"
        assert cell.dollar_delta_mirror is not None
        assert cell.dollar_theta_mirror is not None
        assert cell.dollar_rho_mirror is not None


def test_mirror_greeks_absent_on_legacy_cell() -> None:
    from algotrading.core.provenance import stamp as make_stamp
    a_stamp = make_stamp(
        calc_ts=_TS, code_version="v", config_hashes={},
        source_records=(), source_timestamps=(),
    )
    from algotrading.infra.contracts.tables import ProjectedOptionAnalytics
    legacy_cell = ProjectedOptionAnalytics(
        snapshot_ts=_TS, provider="IBKR", underlying="SX5E",
        tenor_label="3m", maturity_years=0.25, delta_band="30dc",
        target_delta=0.30, log_moneyness=0.05, strike=102.0, forward_price=100.0,
        implied_vol=0.20, total_variance=0.01, price=1.5,
        delta=0.30, gamma=0.02, vega=0.10, theta=-0.05, rho=-0.01,
        dollar_delta=30.0, dollar_gamma=2.0, dollar_vega=0.10,
        dollar_delta_unit="per $1 underlying move",
        dollar_gamma_unit="per 1% underlying move",
        dollar_vega_unit="per 1 vol point",
        model_version="svi-1", pricer_version="px-1",
        source_snapshot_ts=_TS, provenance=a_stamp,
    )
    assert legacy_cell.price_mirror is None
    assert legacy_cell.delta_mirror is None
    assert legacy_cell.theta_mirror is None
    assert legacy_cell.rho_mirror is None
    assert legacy_cell.dollar_delta_mirror is None
    assert legacy_cell.dollar_theta_mirror is None
    assert legacy_cell.dollar_rho_mirror is None


def test_delta_call_minus_delta_put_equals_discount_factor() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    cells = list(result.cells)  # type: ignore[attr-defined]
    checked = 0
    for cell in cells:
        df = math.exp(-term.rate * cell.maturity_years)

        if cell.delta > 0:
            delta_call = cell.delta
            delta_put = cell.delta_mirror
        else:
            delta_put = cell.delta
            delta_call = cell.delta_mirror

        assert delta_put is not None
        assert (delta_call - delta_put) == pytest.approx(df, abs=1e-6), (
            f"{cell.tenor_label}/{cell.delta_band}: ΔC−ΔP={delta_call - delta_put:.8f} "
            f"expected DF={df:.8f}"
        )
        checked += 1
    assert checked > 0, "no cells checked — test is vacuous"


def test_gamma_equals_mirror_gamma() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    cells = list(result.cells)  # type: ignore[attr-defined]
    from algotrading.infra.pricing import from_forward, price_european
    checked = 0
    for cell in cells:
        mirror_right = "P" if cell.delta > 0 else "C"
        df = math.exp(-term.rate * cell.maturity_years)
        mirror_state = from_forward(
            forward=term.forward, strike=cell.strike,
            maturity_years=cell.maturity_years, volatility=cell.implied_vol,
            discount_factor=df, option_right=mirror_right, spot=term.forward,
        )
        mirror_greeks = price_european(mirror_state)
        assert cell.gamma == pytest.approx(mirror_greeks.gamma, rel=1e-9), (
            f"{cell.tenor_label}/{cell.delta_band}: primary_gamma={cell.gamma} "
            f"mirror_gamma={mirror_greeks.gamma}"
        )
        checked += 1
    assert checked > 0


def test_vega_equals_mirror_vega() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    cells = list(result.cells)  # type: ignore[attr-defined]
    from algotrading.infra.pricing import from_forward, price_european
    checked = 0
    for cell in cells:
        mirror_right = "P" if cell.delta > 0 else "C"
        df = math.exp(-term.rate * cell.maturity_years)
        mirror_state = from_forward(
            forward=term.forward, strike=cell.strike,
            maturity_years=cell.maturity_years, volatility=cell.implied_vol,
            discount_factor=df, option_right=mirror_right, spot=term.forward,
        )
        mirror_greeks = price_european(mirror_state)
        assert cell.vega == pytest.approx(mirror_greeks.vega, rel=1e-9), (
            f"{cell.tenor_label}/{cell.delta_band}: primary_vega={cell.vega} "
            f"mirror_vega={mirror_greeks.vega}"
        )
        checked += 1
    assert checked > 0


def test_price_parity_call_minus_put_equals_df_times_f_minus_k() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    cells = list(result.cells)  # type: ignore[attr-defined]
    checked = 0
    for cell in cells:
        df = math.exp(-term.rate * cell.maturity_years)
        if cell.delta > 0:
            call_price = cell.price
            put_price = cell.price_mirror
        else:
            put_price = cell.price
            call_price = cell.price_mirror

        assert put_price is not None
        expected_diff = df * (term.forward - cell.strike)
        assert (call_price - put_price) == pytest.approx(expected_diff, abs=1e-6), (
            f"{cell.tenor_label}/{cell.delta_band}: C−P={call_price - put_price:.8f} "
            f"expected DF·(F−K)={expected_diff:.8f}"
        )
        checked += 1
    assert checked > 0


def test_atm_call_and_put_prices_are_approximately_equal() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    cells = list(result.cells)  # type: ignore[attr-defined]
    found = 0
    for tenor in ("3m", "6m", "12m"):
        atm_call = next(
            (c for c in cells if c.tenor_label == tenor and c.delta_band == "atm"), None
        )
        atm_put = next(
            (c for c in cells if c.tenor_label == tenor and c.delta_band == "atmp"), None
        )
        if atm_call is None or atm_put is None:
            continue
        assert atm_call.strike == pytest.approx(atm_put.strike, rel=1e-9)
        assert atm_call.implied_vol == pytest.approx(atm_put.implied_vol, rel=1e-9)
        df = math.exp(-term.rate * atm_call.maturity_years)
        parity_diff = df * (atm_call.forward_price - atm_call.strike)
        assert (atm_call.price - atm_put.price) == pytest.approx(parity_diff, abs=1e-6), (
            f"{tenor}/atm: C-P={atm_call.price - atm_put.price:.8f}, "
            f"DF*(F-K)={parity_diff:.8f}"
        )
        assert atm_call.price_mirror == pytest.approx(atm_put.price, rel=1e-6)
        assert atm_put.price_mirror == pytest.approx(atm_call.price, rel=1e-6)
        found += 1
    assert found > 0, "no ATM cells found at interior tenors"


def test_delta_mirror_sign_opposes_primary_for_non_atm_bands() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    cells = list(result.cells)  # type: ignore[attr-defined]
    checked = 0
    for cell in cells:
        if cell.delta_band in {"atm", "atmp"}:
            continue
        if cell.delta > 0:
            assert cell.delta_mirror is not None
            assert cell.delta_mirror < 0, (
                f"{cell.tenor_label}/{cell.delta_band}: expected mirror delta < 0, "
                f"got {cell.delta_mirror}"
            )
        else:
            assert cell.delta_mirror is not None
            assert cell.delta_mirror > 0, (
                f"{cell.tenor_label}/{cell.delta_band}: expected mirror delta > 0, "
                f"got {cell.delta_mirror}"
            )
        checked += 1
    assert checked > 0


def test_dollar_delta_mirror_equals_delta_mirror_times_spot() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    cells = list(result.cells)  # type: ignore[attr-defined]
    checked = 0
    for cell in cells:
        assert cell.delta_mirror is not None
        assert cell.dollar_delta_mirror is not None
        expected = cell.delta_mirror * term.forward
        assert cell.dollar_delta_mirror == pytest.approx(expected, rel=1e-9), (
            f"{cell.tenor_label}/{cell.delta_band}: got {cell.dollar_delta_mirror}, "
            f"expected delta_mirror*spot={expected}"
        )
        checked += 1
    assert checked > 0


def test_dollar_theta_mirror_equals_theta_mirror_over_365() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    cells = list(result.cells)  # type: ignore[attr-defined]
    checked = 0
    for cell in cells:
        assert cell.theta_mirror is not None
        assert cell.dollar_theta_mirror is not None
        expected = cell.theta_mirror / 365.0
        assert cell.dollar_theta_mirror == pytest.approx(expected, rel=1e-9), (
            f"{cell.tenor_label}/{cell.delta_band}: got {cell.dollar_theta_mirror}, "
            f"expected theta_mirror/365={expected}"
        )
        checked += 1
    assert checked > 0


def test_dollar_rho_mirror_equals_rho_mirror_times_point01() -> None:
    term = build_synthetic_term_surface()
    result = _project(term)
    cells = list(result.cells)  # type: ignore[attr-defined]
    checked = 0
    for cell in cells:
        assert cell.rho_mirror is not None
        assert cell.dollar_rho_mirror is not None
        expected = cell.rho_mirror * 0.01
        assert cell.dollar_rho_mirror == pytest.approx(expected, rel=1e-9), (
            f"{cell.tenor_label}/{cell.delta_band}: got {cell.dollar_rho_mirror}, "
            f"expected rho_mirror*0.01={expected}"
        )
        checked += 1
    assert checked > 0


def test_serializer_includes_mirror_fields() -> None:
    from algotrading.frontend.serializers import projected_option_analytics_to_dict

    term = build_synthetic_term_surface()
    result = _project(term)
    cells = list(result.cells)  # type: ignore[attr-defined]
    assert cells, "no cells produced — surface degenerate"
    sample = cells[0]
    d = projected_option_analytics_to_dict(sample)

    assert "price_mirror" in d
    assert d["price_mirror"] is not None
    assert "mirror_metrics" in d
    mirror = d["mirror_metrics"]
    assert "delta" in mirror
    assert "theta" in mirror
    assert "rho" in mirror
    for greek in ("delta", "theta", "rho"):
        assert "raw" in mirror[greek]
        assert "dollar" in mirror[greek]
        assert "unit" in mirror[greek]
        assert mirror[greek]["raw"] is not None
        assert mirror[greek]["dollar"] is not None
        assert mirror[greek]["unit"] is not None


def test_serializer_nullable_on_legacy_cell() -> None:
    from algotrading.core.provenance import stamp as make_stamp
    from algotrading.frontend.serializers import projected_option_analytics_to_dict
    from algotrading.infra.contracts.tables import ProjectedOptionAnalytics

    a_stamp = make_stamp(
        calc_ts=_TS, code_version="v", config_hashes={},
        source_records=(), source_timestamps=(),
    )
    legacy_cell = ProjectedOptionAnalytics(
        snapshot_ts=_TS, provider="IBKR", underlying="SX5E",
        tenor_label="3m", maturity_years=0.25, delta_band="30dc",
        target_delta=0.30, log_moneyness=0.05, strike=102.0, forward_price=100.0,
        implied_vol=0.20, total_variance=0.01, price=1.5,
        delta=0.30, gamma=0.02, vega=0.10, theta=-0.05, rho=-0.01,
        dollar_delta=30.0, dollar_gamma=2.0, dollar_vega=0.10,
        dollar_delta_unit="per $1 underlying move",
        dollar_gamma_unit="per 1% underlying move",
        dollar_vega_unit="per 1 vol point",
        model_version="svi-1", pricer_version="px-1",
        source_snapshot_ts=_TS, provenance=a_stamp,
    )
    d = projected_option_analytics_to_dict(legacy_cell)
    assert d["price_mirror"] is None
    mirror = d["mirror_metrics"]
    assert mirror["delta"]["raw"] is None
    assert mirror["delta"]["dollar"] is None
    assert mirror["delta"]["unit"] is None
    assert mirror["theta"]["raw"] is None
    assert mirror["rho"]["raw"] is None


def test_mirror_cells_use_only_snapshot_data() -> None:
    term_low_vol = build_synthetic_term_surface(svi_a_per_year=0.01, svi_b=0.03)
    term_high_vol = build_synthetic_term_surface(svi_a_per_year=0.10, svi_b=0.15)
    result_low = _project(term_low_vol)
    result_high = _project(term_high_vol)

    low_cells = {c.delta_band: c for c in result_low.cells if c.tenor_label == "12m"}  # type: ignore[attr-defined]
    high_cells = {c.delta_band: c for c in result_high.cells if c.tenor_label == "12m"}  # type: ignore[attr-defined]
    assert "atm" in low_cells and "atm" in high_cells, "ATM band missing at 12m"
    low_atm = low_cells["atm"]
    high_atm = high_cells["atm"]
    assert low_atm.price_mirror is not None
    assert high_atm.price_mirror is not None
    ratio = high_atm.price_mirror / low_atm.price_mirror
    assert ratio > 1.5, (
        f"ATM put price barely changed between low-vol and high-vol surfaces "
        f"(ratio={ratio:.3f}); mirror Greeks may not use the snapshot IV"
    )
