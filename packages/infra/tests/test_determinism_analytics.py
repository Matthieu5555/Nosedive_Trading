"""Determinism for Workstream C: golden output, cross-process hashes, reordering.

The acceptance criterion "repeated runs on the same fixtures produce byte-identical
outputs" is backed here by real machinery, per ``tasks/TESTING.md``:

* **Golden file.** The analytics pipeline (synthetic chain -> forward -> IV ->
  surface) is run and its key outputs compared to a committed artifact,
  ``tests/golden/analytics_pipeline.json``. Regeneration is a deliberate, reviewable
  act, never automatic:

      C_REGEN_GOLDEN=1 uv run pytest tests/test_determinism_analytics.py -k golden

  which rewrites the JSON; the change then shows up in ``git diff`` for review.

* **Cross-process hash stability.** The provenance ``stamp_hash`` on a C-emitted
  contract is recomputed in a *separate* Python process and must match in-process.
  This catches the classic bug — hashing a ``dict``/``set`` under hash randomization
  — that passes in-process and drifts between runs. It must hold without
  ``PYTHONHASHSEED`` being set.

* **Reordering invariance.** Shuffling the input pairs must not change the forward or
  its stamp (source records are sorted into a canonical order before hashing).

* **Byte-identical repeats.** Running the whole pipeline twice in one process yields
  equal contracts, value for value.
"""

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
# The analytics modules are installed in the workspace venv, so a fresh interpreter
# imports them directly; the subprocess only needs this tests dir on its path to reach
# the shared `fixtures` package and this module's `compute_pipeline_summary`.
_TESTS_DIR = str(Path(__file__).resolve().parent)


def _forward_pairs(surface: Any) -> tuple[ForwardPair, ...]:
    return tuple(
        ForwardPair(strike=p.strike, call_mid=p.call_price, put_mid=p.put_price, liquidity=1.0,
                    call_key=f"AAPL|OPT|C|{p.strike:g}", put_key=f"AAPL|OPT|P|{p.strike:g}")
        for p in surface.points
    )


def compute_pipeline_summary() -> dict[str, Any]:
    """Run the full C pipeline on the synthetic chain and summarize its outputs.

    Shared by the golden test, the byte-identical-repeat test, and the cross-process
    subprocess (which imports it), so all three exercise exactly the same path.
    """
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
        "forward": fwd.forward,
        "discount_factor": estimate.discount_factor,
        "forward_stamp_hash": fwd.provenance.stamp_hash,
        "iv_by_strike": {
            f"{p.strike:g}": pt.iv
            for p, pt in zip(surface.points, iv_points, strict=True)
        },
        "svi": {"a": params.svi_a, "b": params.svi_b, "rho": params.svi_rho,
                "m": params.svi_m, "sigma": params.svi_sigma},
        "surface_stamp_hash": params.provenance.stamp_hash,
        "grid_total_variance_at_atm": grid.total_variance,
    }


# --------------------------------------------------------------------------- #
# Golden artifact                                                             #
# --------------------------------------------------------------------------- #
def test_golden_pipeline_matches_committed_artifact() -> None:
    summary = compute_pipeline_summary()
    if os.environ.get("C_REGEN_GOLDEN"):
        _GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"regenerated golden artifact at {_GOLDEN_PATH}")

    assert _GOLDEN_PATH.exists(), (
        f"missing golden artifact; regenerate with "
        f"C_REGEN_GOLDEN=1 uv run pytest {Path(__file__).name} -k golden"
    )
    golden = json.loads(_GOLDEN_PATH.read_text())

    # Lineage hashes must match byte-for-byte (the determinism handle).
    assert summary["forward_stamp_hash"] == golden["forward_stamp_hash"]
    assert summary["surface_stamp_hash"] == golden["surface_stamp_hash"]
    # Numeric outputs must match to well past any plausible cross-machine drift.
    assert summary["forward"] == pytest.approx(golden["forward"], rel=1e-12)
    assert summary["discount_factor"] == pytest.approx(golden["discount_factor"], rel=1e-12)
    assert summary["grid_total_variance_at_atm"] == pytest.approx(
        golden["grid_total_variance_at_atm"], rel=1e-9
    )
    for strike, iv in summary["iv_by_strike"].items():
        assert iv == pytest.approx(golden["iv_by_strike"][strike], rel=1e-9)
    for name, value in summary["svi"].items():
        assert value == pytest.approx(golden["svi"][name], abs=1e-6)


# --------------------------------------------------------------------------- #
# Byte-identical repeats and reordering invariance                            #
# --------------------------------------------------------------------------- #
def test_repeated_runs_are_byte_identical() -> None:
    # The strongest in-process determinism claim: run twice, compare value for value.
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
    assert forward_a.forward == forward_b.forward
    # Source records are canonicalized before hashing, so the stamp is order-free.
    assert forward_a.provenance.stamp_hash == forward_b.provenance.stamp_hash


# --------------------------------------------------------------------------- #
# Cross-process hash stability                                                #
# --------------------------------------------------------------------------- #
_SUBPROCESS_SCRIPT = """
import json
from test_determinism_analytics import compute_pipeline_summary
print(json.dumps(compute_pipeline_summary()))
"""


def test_pipeline_hashes_are_stable_across_processes() -> None:
    # Recompute the pipeline in a *separate* interpreter (no inherited state, no
    # PYTHONHASHSEED set) and require identical stamp hashes. This catches a stamp
    # built from a salted hash()/set ordering, which would pass in-process and drift.
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
