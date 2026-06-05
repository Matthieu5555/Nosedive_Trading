"""Config hashing: determinism, cross-process stability, section isolation, overlays.

The config hash is the reproducibility handle branded onto every output. These tests
pin that it is content-addressed (order-independent), stable across processes (no
``hash()`` salt leak), isolates sections, and that the YAML overlay loader merges
deterministically.
"""

from __future__ import annotations

import os
import subprocess
import sys

from algotrading.core import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
    composite_config_hash,
    config_hash,
    load_yaml_config,
    section_hash,
    section_versions,
)


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", underlyings=("SPX", "NDX"), exchange="CBOE"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.05, max_quote_age_seconds=30.0, min_chain_count=5
        ),
        solver=SolverConfig(version="s-1", iv_tolerance=1e-8, max_iterations=100),
        scenario=ScenarioConfig(
            version="sc-1", spot_shocks=(-0.1, 0.0, 0.1), vol_shocks=(-0.02, 0.02)
        ),
    )


def test_config_hash_is_deterministic() -> None:
    assert config_hash(_config()) == config_hash(_config())


def test_config_hash_moves_when_any_field_moves() -> None:
    import dataclasses

    base = _config()
    moved = dataclasses.replace(
        base, solver=dataclasses.replace(base.solver, iv_tolerance=1e-9)
    )
    assert config_hash(moved) != config_hash(base)


def test_section_hash_isolates_one_section() -> None:
    import dataclasses

    base = _config()
    # Bump only the scenario grid: scenario hash moves, solver hash does not.
    moved = dataclasses.replace(
        base, scenario=dataclasses.replace(base.scenario, vol_shocks=(-0.03, 0.03))
    )
    assert section_hash(moved, "scenario") != section_hash(base, "scenario")
    assert section_hash(moved, "solver") == section_hash(base, "solver")


def test_section_versions_lists_the_four_stamps() -> None:
    assert section_versions(_config()) == {
        "universe": "u-1",
        "qc_threshold": "qc-1",
        "solver": "s-1",
        "scenario": "sc-1",
    }


def test_composite_config_hash_is_order_independent_and_sensitive() -> None:
    a = composite_config_hash({"qc": "h1", "forward": "h2"})
    b = composite_config_hash({"forward": "h2", "qc": "h1"})
    assert a == b
    assert composite_config_hash({"qc": "h1", "forward": "CHANGED"}) != a


def test_config_hash_is_stable_across_processes() -> None:
    # No PYTHONHASHSEED dependence (TESTING.md cross-process requirement).
    expected = config_hash(_config())
    code = (
        "from algotrading.core import (PlatformConfig, UniverseConfig, QcThresholdConfig,"
        " SolverConfig, ScenarioConfig, config_hash);"
        "print(config_hash(PlatformConfig("
        "universe=UniverseConfig(version='u-1', underlyings=('SPX','NDX'), exchange='CBOE'),"
        "qc_threshold=QcThresholdConfig(version='qc-1', max_spread_pct=0.05,"
        " max_quote_age_seconds=30.0, min_chain_count=5),"
        "solver=SolverConfig(version='s-1', iv_tolerance=1e-8, max_iterations=100),"
        "scenario=ScenarioConfig(version='sc-1', spot_shocks=(-0.1,0.0,0.1),"
        " vol_shocks=(-0.02,0.02)))))"
    )
    for seed in ("0", "7", "98765"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert out.stdout.strip() == expected, f"config hash drifted under seed {seed}"


def test_yaml_overlay_merges_deterministically(tmp_path) -> None:
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "prod.yaml"
    base.write_text("a: 1\nnested:\n  x: 1\n  y: 2\n", encoding="utf-8")
    overlay.write_text("nested:\n  y: 99\nb: 2\n", encoding="utf-8")

    loaded = load_yaml_config(overlay, base=base)
    assert loaded.data["a"] == 1
    assert loaded.data["b"] == 2
    assert loaded.data["nested"]["x"] == 1
    assert loaded.data["nested"]["y"] == 99  # overlay wins
    # Re-loading the same files yields the same content hash.
    assert load_yaml_config(overlay, base=base).config_hash == loaded.config_hash
