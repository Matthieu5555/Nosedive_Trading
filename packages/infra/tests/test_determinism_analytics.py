from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from algotrading.core.config import SolverConfig
from algotrading.infra.forwards import ForwardPair, estimate_forward, forward_curve_point
from algotrading.infra.iv import iv_point, solve_iv
from algotrading.infra.surfaces import fit_slice, surface_grid_cells, surface_parameters
from fixtures.library import FORWARD_CONFIG, SURFACE_CONFIG
from fixtures.synthetic import build_synthetic_surface

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
EXPIRY = date(2026, 6, 19)
CONFIG_HASH = {"cfg": "cfg-hash-0"}
SOLVER = SolverConfig(version="iv-1", iv_tolerance=1e-12, max_iterations=200)
_GOLDEN_PATH = Path(__file__).parent / "golden" / "analytics_pipeline.json"
_TESTS_DIR = str(Path(__file__).resolve().parent)


def _forward_pairs(surface: Any) -> tuple[ForwardPair, ...]:
    return tuple(
        ForwardPair(strike=p.strike, call_mid=p.call_price, put_mid=p.put_price, liquidity=1.0,
                    call_key=f"AAPL|OPT|C|{p.strike:g}", put_key=f"AAPL|OPT|P|{p.strike:g}")
        for p in surface.points
    )


def compute_pipeline_summary() -> dict[str, Any]:
    surface = build_synthetic_surface()
    spot = surface.forward * surface.discount_factor

    estimate = estimate_forward(
        "AAPL", surface.maturity_years, _forward_pairs(surface), config=FORWARD_CONFIG, spot=spot
    )
    fwd = forward_curve_point(estimate, snapshot_ts=TS, expiry_date=EXPIRY, day_count="ACT/365",
                              source_snapshot_ts=TS, calc_ts=TS, config_hashes=CONFIG_HASH)

    iv_points = []
    for p in surface.points:
        result = solve_iv(p.call_price, contract_key=f"AAPL|OPT|C|{p.strike:g}",
                          forward=surface.forward, strike=p.strike,
                          maturity_years=surface.maturity_years,
                          discount_factor=surface.discount_factor, option_right="C", config=SOLVER)
        iv_points.append(iv_point(result, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
                                  config_hashes=CONFIG_HASH))

    fit = fit_slice("AAPL", surface.maturity_years, tuple(iv_points), expiry_date=EXPIRY,
                    day_count="ACT/365", config=SURFACE_CONFIG)
    params = surface_parameters(fit, snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
                                config_hashes=CONFIG_HASH)
    grid = surface_grid_cells(fit, (0.0,), snapshot_ts=TS, source_snapshot_ts=TS, calc_ts=TS,
                              config_hashes=CONFIG_HASH)[0]

    return {
        "forward": fwd.forward_price,
        "discount_factor": estimate.discount_factor,
        "forward_stamp_hash": fwd.provenance.stamp_hash,
        "iv_by_strike": {
            f"{p.strike:g}": pt.implied_vol
            for p, pt in zip(surface.points, iv_points, strict=True)
        },
        "svi": {"a": params.svi_a, "b": params.svi_b, "rho": params.svi_rho,
                "m": params.svi_m, "sigma": params.svi_sigma},
        "surface_stamp_hash": params.provenance.stamp_hash,
        "grid_total_variance_at_atm": grid.total_variance,
    }


def test_golden_pipeline_matches_committed_artifact(golden_artifact: Any) -> None:
    summary = compute_pipeline_summary()
    golden = golden_artifact(_GOLDEN_PATH, summary)

    assert summary["forward_stamp_hash"] == golden["forward_stamp_hash"]
    assert summary["surface_stamp_hash"] == golden["surface_stamp_hash"]
    assert summary["forward"] == pytest.approx(golden["forward"], rel=1e-12)
    assert summary["discount_factor"] == pytest.approx(golden["discount_factor"], rel=1e-12)
    assert summary["grid_total_variance_at_atm"] == pytest.approx(
        golden["grid_total_variance_at_atm"], rel=1e-9
    )
    for strike, iv in summary["iv_by_strike"].items():
        assert iv == pytest.approx(golden["iv_by_strike"][strike], rel=1e-9)
    for name, value in summary["svi"].items():
        assert value == pytest.approx(golden["svi"][name], abs=1e-6)


def test_repeated_runs_are_byte_identical() -> None:
    assert compute_pipeline_summary() == compute_pipeline_summary()


def test_forward_is_invariant_to_input_pair_order() -> None:
    surface = build_synthetic_surface()
    spot = surface.forward * surface.discount_factor
    pairs = _forward_pairs(surface)

    forward_a = forward_curve_point(
        estimate_forward("AAPL", surface.maturity_years, pairs, config=FORWARD_CONFIG, spot=spot),
        snapshot_ts=TS, expiry_date=EXPIRY, day_count="ACT/365", source_snapshot_ts=TS,
        calc_ts=TS, config_hashes=CONFIG_HASH,
    )
    forward_b = forward_curve_point(
        estimate_forward(
            "AAPL", surface.maturity_years, tuple(reversed(pairs)), config=FORWARD_CONFIG, spot=spot
        ),
        snapshot_ts=TS, expiry_date=EXPIRY, day_count="ACT/365", source_snapshot_ts=TS,
        calc_ts=TS, config_hashes=CONFIG_HASH,
    )
    assert forward_a.forward_price == forward_b.forward_price
    assert forward_a.provenance.stamp_hash == forward_b.provenance.stamp_hash


_SUBPROCESS_SCRIPT = """
import json
from test_determinism_analytics import compute_pipeline_summary
print(json.dumps(compute_pipeline_summary()))
"""


def test_pipeline_hashes_are_stable_across_processes() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = _TESTS_DIR
    env.pop("PYTHONHASHSEED", None)
    completed = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT],
        capture_output=True, text=True, env=env, check=True,
    )
    other = json.loads(completed.stdout)
    here = compute_pipeline_summary()
    assert other["forward_stamp_hash"] == here["forward_stamp_hash"]
    assert other["surface_stamp_hash"] == here["surface_stamp_hash"]
    assert other["forward"] == here["forward"]
