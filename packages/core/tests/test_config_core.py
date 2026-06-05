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

import pytest
from algotrading.core import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
    composite_config_hash,
    config_hash,
    from_config,
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


_BASE_ECONOMIC_YAML = """\
universe:
  version: u-base
  underlyings: [SPX, NDX]
  exchange: CBOE
qc_threshold:
  version: qc-base
  max_spread_pct: 0.05
  max_quote_age_seconds: 30.0
  min_chain_count: 5
solver:
  version: s-base
  iv_tolerance: 1.0e-8
  max_iterations: 100
scenario:
  version: sc-base
  spot_shocks: [-0.1, 0.0, 0.1]
  vol_shocks: [-0.02, 0.02]
"""


def test_from_config_builds_typed_platform_config_over_a_yaml_overlay(tmp_path) -> None:
    # The typed economic config builds from a versioned YAML base + one overlay through
    # the same validation as the TOML path (C7 task 1: one schema, the overlay loader's
    # inheritance). The overlay narrows the universe (a list, replaced wholesale, not
    # merged) and tightens one qc threshold; everything else is inherited from the base.
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "single_name.yaml"
    base.write_text(_BASE_ECONOMIC_YAML, encoding="utf-8")
    overlay.write_text(
        "universe:\n  underlyings: [AAPL]\nqc_threshold:\n  max_spread_pct: 0.02\n",
        encoding="utf-8",
    )

    config = from_config(load_yaml_config(overlay, base=base))

    assert isinstance(config, PlatformConfig)
    # List value replaced wholesale by the overlay (not ["SPX","NDX","AAPL"]), as a tuple.
    assert config.universe.underlyings == ("AAPL",)
    # Scalar overridden by the overlay.
    assert config.qc_threshold.max_spread_pct == 0.02
    # Untouched fields inherited from the base, with the dataclasses' coerced types.
    assert config.universe.version == "u-base"
    assert config.universe.exchange == "CBOE"
    assert config.qc_threshold.max_quote_age_seconds == 30.0
    assert config.qc_threshold.min_chain_count == 5
    assert config.solver.iv_tolerance == 1e-8
    assert config.solver.max_iterations == 100
    assert config.scenario.spot_shocks == (-0.1, 0.0, 0.1)
    assert config.scenario.vol_shocks == (-0.02, 0.02)


def test_from_config_rejects_a_missing_section(tmp_path) -> None:
    # A resolved config missing a required economic section fails loudly with the typed
    # ConfigError (naming the section), never a bare KeyError from deep in the builder.
    from algotrading.core.config import ConfigError

    incomplete = tmp_path / "incomplete.yaml"
    incomplete.write_text("universe:\n  version: u\n  underlyings: [SPX]\n  exchange: CBOE\n", "utf-8")
    with pytest.raises(ConfigError):
        from_config(load_yaml_config(incomplete))


def test_config_hash_collapses_signed_zero() -> None:
    # -0.0 and 0.0 are mathematically equal; a reproducibility hash must not split them
    # (they serialize to different JSON tokens without normalization).
    import dataclasses

    base = _config()
    neg = dataclasses.replace(base, scenario=dataclasses.replace(base.scenario, vol_shocks=(-0.0, 0.05)))
    pos = dataclasses.replace(base, scenario=dataclasses.replace(base.scenario, vol_shocks=(0.0, 0.05)))
    assert config_hash(neg) == config_hash(pos)


def test_mapping_hash_collapses_signed_zero() -> None:
    from algotrading.core.config import mapping_config_hash

    assert mapping_config_hash({"shock": -0.0}) == mapping_config_hash({"shock": 0.0})


def test_canonical_json_and_mapping_hash_reject_non_finite() -> None:
    # A reproducibility hash must never emit invalid JSON (NaN/Infinity are not JSON).
    from algotrading.core.config import canonical_json, mapping_config_hash

    with pytest.raises(ValueError):
        canonical_json([float("nan")])
    with pytest.raises(ValueError):
        canonical_json([float("inf")])
    with pytest.raises(ValueError):
        mapping_config_hash({"x": float("nan")})
    with pytest.raises(ValueError):
        mapping_config_hash({"x": float("-inf")})
