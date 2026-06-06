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


# -- load_platform_config: the six-bundle Part VII loader (C7 increment 1) -----------

# The four economic bundle files, each authored as one file per the blueprint Part VII
# taxonomy. environment.yaml + broker.yaml are operational and must NOT be loaded into
# the hashed typed config — the loader ignores them.
_BUNDLES = {
    "universe.yaml": "version: u-1\nunderlyings: [SPX, NDX]\nexchange: CBOE\n",
    "qc.yaml": "version: qc-1\nmax_spread_pct: 0.05\nmax_quote_age_seconds: 30.0\nmin_chain_count: 5\n",
    "pricing.yaml": "version: s-1\niv_tolerance: 1.0e-8\nmax_iterations: 100\n",
    "scenarios.yaml": "version: sc-1\nspot_shocks: [-0.1, 0.0, 0.1]\nvol_shocks: [-0.02, 0.02]\n",
}


def _write_bundles(configs_dir, *, extra: dict[str, str] | None = None) -> None:
    configs_dir.mkdir(parents=True, exist_ok=True)
    for name, text in {**_BUNDLES, **(extra or {})}.items():
        (configs_dir / name).write_text(text, encoding="utf-8")


def test_load_platform_config_assembles_the_six_bundles(tmp_path) -> None:
    # The four economic bundles compose into one validated PlatformConfig, by the same
    # values an equivalent hand-built config carries — so the per-file split is purely a
    # layout choice, not a semantic one.
    from algotrading.core.config import load_platform_config

    _write_bundles(tmp_path)
    config = load_platform_config(tmp_path)

    assert isinstance(config, PlatformConfig)
    assert config.universe.underlyings == ("SPX", "NDX")        # universe.yaml
    assert config.qc_threshold.min_chain_count == 5             # qc.yaml
    assert config.solver.iv_tolerance == 1e-8                   # pricing.yaml → solver section
    assert config.scenario.spot_shocks == (-0.1, 0.0, 0.1)     # scenarios.yaml
    # Hashable and equal to the same config built directly: layout does not change content.
    assert config_hash(config) == config_hash(
        PlatformConfig(
            universe=UniverseConfig(version="u-1", underlyings=("SPX", "NDX"), exchange="CBOE"),
            qc_threshold=QcThresholdConfig(
                version="qc-1", max_spread_pct=0.05, max_quote_age_seconds=30.0, min_chain_count=5
            ),
            solver=SolverConfig(version="s-1", iv_tolerance=1e-8, max_iterations=100),
            scenario=ScenarioConfig(
                version="sc-1", spot_shocks=(-0.1, 0.0, 0.1), vol_shocks=(-0.02, 0.02)
            ),
        )
    )


def test_load_platform_config_ignores_the_operational_bundles(tmp_path) -> None:
    # environment.yaml + broker.yaml are operational (not hashed) — present in a real
    # configs/ dir but never loaded into the typed economic config. A junk economic field
    # inside one of them must not reach validation (the loader does not read them).
    from algotrading.core.config import load_platform_config

    _write_bundles(
        tmp_path,
        extra={
            "environment.yaml": "version: e\nstorage:\n  root: data\n",
            "broker.yaml": "version: b\nnonsense_economic_field: 1\n",
        },
    )
    config = load_platform_config(tmp_path)
    assert isinstance(config, PlatformConfig)


def test_load_platform_config_names_a_missing_bundle(tmp_path) -> None:
    # A misconfigured deployment fails loudly: a missing economic bundle raises the typed
    # ConfigError naming the file, never a bare FileNotFoundError from deep inside.
    from algotrading.core.config import ConfigError, load_platform_config

    _write_bundles(tmp_path)
    (tmp_path / "pricing.yaml").unlink()
    with pytest.raises(ConfigError, match="pricing.yaml"):
        load_platform_config(tmp_path)


def test_load_platform_config_loads_the_shipped_bundles() -> None:
    # The real checked-in configs/ dir loads and hashes — production runs this exact path.
    from pathlib import Path

    from algotrading.core.config import load_platform_config

    repo_root = Path(__file__).resolve().parents[3]
    config = load_platform_config(repo_root / "configs")
    assert config.universe.underlyings, "the shipped universe bundle must name underlyings"
    assert isinstance(config_hash(config), str)


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


_GOOD_QC = {
    "version": "qc-1",
    "max_spread_pct": 0.05,
    "max_quote_age_seconds": 30.0,
    "min_chain_count": 6,
}


def test_build_dataclass_coerces_by_declared_type() -> None:
    from algotrading.core.config import build_dataclass

    qc = build_dataclass(QcThresholdConfig, _GOOD_QC, section="qc_threshold")
    assert qc.max_spread_pct == 0.05 and isinstance(qc.min_chain_count, int)
    # tuple[float, ...] coercion: a YAML list of numbers becomes a tuple of floats.
    sc = build_dataclass(
        ScenarioConfig,
        {"version": "sc", "spot_shocks": [-0.1, 0, 0.1], "vol_shocks": [0.0]},
        section="scenario",
    )
    assert sc.spot_shocks == (-0.1, 0.0, 0.1)
    assert all(isinstance(x, float) for x in sc.spot_shocks)


def test_build_dataclass_rejects_unknown_key() -> None:
    from algotrading.core.config import ConfigFieldError, build_dataclass

    with pytest.raises(ConfigFieldError) as exc:
        build_dataclass(QcThresholdConfig, {**_GOOD_QC, "typo": 1}, section="qc_threshold")
    assert exc.value.field == "typo"


def test_build_dataclass_rejects_missing_field() -> None:
    from algotrading.core.config import ConfigFieldError, build_dataclass

    incomplete = {k: v for k, v in _GOOD_QC.items() if k != "min_chain_count"}
    with pytest.raises(ConfigFieldError) as exc:
        build_dataclass(QcThresholdConfig, incomplete, section="qc_threshold")
    assert exc.value.field == "min_chain_count" and "missing" in exc.value.reason


def test_build_dataclass_rejects_fractional_int() -> None:
    from algotrading.core.config import ConfigFieldError, build_dataclass

    with pytest.raises(ConfigFieldError) as exc:
        build_dataclass(QcThresholdConfig, {**_GOOD_QC, "min_chain_count": 6.5}, section="qc_threshold")
    assert exc.value.field == "min_chain_count"


def test_post_init_range_validation_raises_labelled_error() -> None:
    from algotrading.core.config import ConfigFieldError, build_dataclass

    with pytest.raises(ConfigFieldError) as exc:
        build_dataclass(QcThresholdConfig, {**_GOOD_QC, "max_spread_pct": -0.01}, section="qc_threshold")
    assert exc.value.section == "qc_threshold"
    assert exc.value.field == "max_spread_pct"
    assert exc.value.value == -0.01


def test_from_config_surfaces_a_bad_economic_value(tmp_path) -> None:
    # A bad value in the YAML must fail loudly through from_config with the labelled error,
    # never a silent default or a bare ValueError deep in the builder.
    from algotrading.core.config import ConfigFieldError

    bad = tmp_path / "bad.yaml"
    base = tmp_path / "base.yaml"
    base.write_text(_BASE_ECONOMIC_YAML, encoding="utf-8")
    # min_chain_count 0 violates the __post_init__ >= 1 rule.
    bad.write_text("qc_threshold:\n  min_chain_count: 0\n", encoding="utf-8")
    with pytest.raises(ConfigFieldError) as exc:
        from_config(load_yaml_config(bad, base=base))
    assert exc.value.section == "qc_threshold" and exc.value.field == "min_chain_count"
