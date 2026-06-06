"""Determinism for the risk engine: golden risk output, cross-process hashes, reorder.

The risk invariant is determinism and provenance on every risk and scenario output, with
the headline guarantee that worst-case loss reproduces under a pinned scenario version
(``tasks/M3-risk-engine.md``). Backed by real machinery, per TESTING.md:

* **Golden file.** The pf-risk portfolio is run through aggregation and the scenario
  grid and its outputs compared to ``golden/risk_pf_risk.json``. Regenerate deliberately
  (the diff is then reviewed):

      RISK_REGEN_GOLDEN=1 uv run pytest packages/infra/tests/test_determinism_risk.py -k golden

* **Cross-process hash stability.** The ``stamp_hash`` on an emitted contract is
  recomputed in a separate interpreter (no inherited state, ``PYTHONHASHSEED`` unset) and
  must match — catching a stamp built from a salted ``hash()``/``set``.

* **Reordering invariance.** Shuffling the input positions changes neither the aggregate
  nor its stamp.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from algotrading.core.config import ScenarioConfig
from algotrading.core.provenance import ProvenanceStamp, source_ref, stamp
from algotrading.infra.risk import (
    RISK_ENGINE_VERSION,
    LotConsistencyError,
    aggregate_lines,
    effective_scenario_version,
    net_lots,
    position_risk,
    risk_aggregate,
    scenario_grid,
    scenario_line_pnls,
    scenario_result,
    worst_case,
)
from fixtures.positions import CALL_100, PUT_100, RISK_VALUATIONS, risk_positions

TS = datetime(2026, 5, 29, 15, 30, tzinfo=UTC)
CONFIG_HASH = {"cfg": "cfg-hash-0"}
SCENARIO_CONFIG = ScenarioConfig(
    version="scn-1", spot_shocks=(-0.05, 0.05), vol_shocks=(0.05, -0.05)
)
_GOLDEN_PATH = Path(__file__).parent / "golden" / "risk_pf_risk.json"
# The tests directory, so a subprocess can import this module and the fixtures package
# (algotrading.* is installed editable in the workspace venv and needs no path help).
_TESTS_DIR = str(Path(__file__).resolve().parent)


def _lines() -> list:
    return [
        position_risk(
            portfolio_id="pf-risk",
            quantity=p.quantity,
            valuation=RISK_VALUATIONS[p.contract_key],
        )
        for p in risk_positions()
    ]


def _stamp_for(contract_keys: tuple[str, ...]) -> ProvenanceStamp:
    """A stamp whose sources are the priced contracts — order-free by construction."""
    return stamp(
        calc_ts=TS,
        code_version=RISK_ENGINE_VERSION,
        config_hashes=CONFIG_HASH,
        source_records=tuple(
            source_ref("market_state_snapshots", TS, key) for key in contract_keys
        ),
        source_timestamps=(TS,),
    )


def compute_risk_summary() -> dict[str, Any]:
    """Run aggregation and the scenario engine on pf-risk and summarize the outputs.

    Shared by the golden test, the byte-identical repeat, and the cross-process
    subprocess, so all three exercise the same path.
    """
    lines = _lines()
    keys = tuple(line.contract_key for line in lines)
    net = aggregate_lines(lines, portfolio_id="pf-risk", dimension="underlying")[0]
    agg = risk_aggregate(net, valuation_ts=TS, source_snapshot_ts=TS, provenance=_stamp_for(keys))

    grid = scenario_grid(SCENARIO_CONFIG)
    cells = scenario_line_pnls(lines, grid)
    scenario_version = effective_scenario_version(SCENARIO_CONFIG)
    results = [
        scenario_result(
            cell,
            valuation_ts=TS,
            scenario_version=scenario_version,
            source_snapshot_ts=TS,
            provenance=_stamp_for((cell.line.contract_key,)),
        )
        for cell in cells
    ]
    wc = worst_case(cells)
    return {
        "net_delta": agg.net_delta,
        "net_gamma": agg.net_gamma,
        "net_vega": agg.net_vega,
        "net_theta": agg.net_theta,
        "aggregate_stamp_hash": agg.provenance.stamp_hash,
        "scenario_version": scenario_version,
        "scenario_pnl": {f"{r.scenario_id}|{r.contract_key}": r.scenario_pnl for r in results},
        "scenario_result_count": len(results),
        "first_scenario_stamp_hash": results[0].provenance.stamp_hash,
        "worst_case_scenario": wc.scenario.scenario_id,
        "worst_case_total": wc.total_pnl,
    }


# --- Golden artifact ---------------------------------------------------------
def test_golden_risk_matches_committed_artifact() -> None:
    summary = compute_risk_summary()
    if os.environ.get("RISK_REGEN_GOLDEN"):
        _GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _GOLDEN_PATH.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"regenerated golden artifact at {_GOLDEN_PATH}")

    assert _GOLDEN_PATH.exists(), (
        f"missing golden artifact; regenerate with "
        f"RISK_REGEN_GOLDEN=1 uv run pytest {Path(__file__).name} -k golden"
    )
    golden = json.loads(_GOLDEN_PATH.read_text())
    # Lineage hashes match byte-for-byte (the determinism handle).
    assert summary["aggregate_stamp_hash"] == golden["aggregate_stamp_hash"]
    assert summary["first_scenario_stamp_hash"] == golden["first_scenario_stamp_hash"]
    # Worst case reproduces under the pinned scenario version (the headline guarantee).
    assert summary["worst_case_scenario"] == golden["worst_case_scenario"]
    assert summary["worst_case_total"] == pytest.approx(golden["worst_case_total"], rel=1e-9)
    assert summary["scenario_result_count"] == golden["scenario_result_count"]
    # The persisted scenario version is pinned: a grid-construction change moves it.
    assert summary["scenario_version"] == golden["scenario_version"]
    for key, pnl in summary["scenario_pnl"].items():
        assert pnl == pytest.approx(golden["scenario_pnl"][key], rel=1e-9)
    for greek in ("net_delta", "net_gamma", "net_vega", "net_theta"):
        assert summary[greek] == pytest.approx(golden[greek], rel=1e-9)


# --- Byte-identical repeats and reordering invariance ------------------------
def test_repeated_runs_are_byte_identical() -> None:
    assert compute_risk_summary() == compute_risk_summary()


def test_aggregate_is_invariant_to_input_position_order() -> None:
    lines = _lines()
    keys = tuple(line.contract_key for line in lines)
    forward = risk_aggregate(
        aggregate_lines(lines, portfolio_id="pf-risk", dimension="underlying")[0],
        valuation_ts=TS, source_snapshot_ts=TS, provenance=_stamp_for(keys),
    )
    reversed_lines = list(reversed(lines))
    backward = risk_aggregate(
        aggregate_lines(reversed_lines, portfolio_id="pf-risk", dimension="underlying")[0],
        valuation_ts=TS, source_snapshot_ts=TS,
        provenance=_stamp_for(tuple(line.contract_key for line in reversed_lines)),
    )
    assert forward.net_delta == backward.net_delta
    assert forward.net_gamma == backward.net_gamma
    # Source records are canonicalized before hashing, so the stamp is order-free too.
    assert forward.provenance.stamp_hash == backward.provenance.stamp_hash


# --- Duplicate-lot determinism (ADR 0006, decision 7) ------------------------
# The Position contract carries a `source`, so one contract can arrive as several lots.
# The derived contracts have no lot dimension, so risk nets lots into one canonical line
# per contract — and that ordering must not depend on the order lots arrive in, which is
# exactly what byte-identical replay needs (live vs. stored events need not preserve
# position order). Without netting these lines sort only by contract_key and keep caller
# order; this is the regression that guards it.
def _dup_lot_lines() -> list:
    """A book holding C100 as two lots (4 + 6) plus one P100 lot."""
    return [
        position_risk(portfolio_id="pf-risk", quantity=4.0, valuation=CALL_100),
        position_risk(portfolio_id="pf-risk", quantity=6.0, valuation=CALL_100),
        position_risk(portfolio_id="pf-risk", quantity=-5.0, valuation=PUT_100),
    ]


def test_net_lots_collapses_same_contract_lots() -> None:
    netted = net_lots(_dup_lot_lines())
    # One canonical line per contract, sorted by key; the two C100 lots net to 10.
    assert [line.contract_key for line in netted] == ["AAPL|OPT|C|100", "AAPL|OPT|P|100"]
    call = next(line for line in netted if line.contract_key == "AAPL|OPT|C|100")
    assert call.quantity == 10.0


def test_duplicate_lots_are_order_independent() -> None:
    lines = _dup_lot_lines()
    reversed_lines = list(reversed(lines))

    def agg(book: list) -> tuple:
        net = aggregate_lines(book, portfolio_id="pf-risk", dimension="underlying")[0]
        return (net.net_delta, net.net_gamma, net.net_vega, net.net_theta)

    # Netting a duplicate lot must equal pricing the merged quantity in one lot.
    merged = [
        position_risk(portfolio_id="pf-risk", quantity=10.0, valuation=CALL_100),
        position_risk(portfolio_id="pf-risk", quantity=-5.0, valuation=PUT_100),
    ]
    assert agg(lines) == agg(reversed_lines) == agg(merged)

    grid = scenario_grid(SCENARIO_CONFIG)

    def cell_keys(book: list) -> list:
        return [
            (c.scenario.scenario_id, c.line.contract_key, c.full_reprice_pnl)
            for c in scenario_line_pnls(book, grid)
        ]

    # Ordered cells are byte-identical under reversal, and one cell per contract per
    # scenario — no duplicate (scenario, contract) keys from the two C100 lots.
    assert cell_keys(lines) == cell_keys(reversed_lines) == cell_keys(merged)


def test_inconsistent_lots_are_rejected() -> None:
    # Same contract_key, divergent market state (a corrupt upstream join) must not be
    # silently collapsed onto one lot's valuation.
    other_state = dataclasses.replace(CALL_100, volatility=CALL_100.volatility + 0.05)
    book = [
        position_risk(portfolio_id="pf-risk", quantity=4.0, valuation=CALL_100),
        position_risk(portfolio_id="pf-risk", quantity=6.0, valuation=other_state),
    ]
    with pytest.raises(LotConsistencyError) as info:
        net_lots(book)
    assert info.value.contract_key == "AAPL|OPT|C|100"


# --- Cross-process hash stability --------------------------------------------
_SUBPROCESS_SCRIPT = """
import json
from test_determinism_risk import compute_risk_summary
print(json.dumps(compute_risk_summary()))
"""


def test_risk_hashes_are_stable_across_processes() -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([_TESTS_DIR, env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    env.pop("PYTHONHASHSEED", None)
    completed = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT],
        capture_output=True, text=True, env=env, check=True,
    )
    other = json.loads(completed.stdout)
    here = compute_risk_summary()
    assert other["aggregate_stamp_hash"] == here["aggregate_stamp_hash"]
    assert other["first_scenario_stamp_hash"] == here["first_scenario_stamp_hash"]
    assert other["worst_case_total"] == here["worst_case_total"]
