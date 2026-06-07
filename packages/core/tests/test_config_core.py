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
    ForwardConfig,
    Manifest,
    ManifestValidationError,
    MonetizationConfig,
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    StressSurfaceConfig,
    SurfaceConfig,
    UniverseConfig,
    composite_config_hash,
    config_hash,
    config_hashes,
    config_snapshot,
    from_config,
    load_yaml_config,
    section_hash,
    section_versions,
    validate_manifest,
)
from algotrading.core.config import config_from_mapping


def _surface(version: str = "surf-1") -> SurfaceConfig:
    return SurfaceConfig(
        version=version,
        svi_a_bounds=(0.0, 10.0),
        svi_b_bounds=(1e-8, 10.0),
        svi_rho_bounds=(-0.999, 0.999),
        svi_m_bounds=(-5.0, 5.0),
        svi_sigma_bounds=(1e-8, 10.0),
        svi_bound_hit_tol=1e-5,
        svi_max_iterations=200,
    )


def _forward(version: str = "fwd-1") -> ForwardConfig:
    return ForwardConfig(
        version=version,
        good_rel_residual=1e-3,
        fair_rel_residual=1e-2,
        full_credit_pairs=4.0,
        rel_residual_halflife=1e-3,
        single_pair_confidence=0.30,
    )


def _config() -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u-1", underlyings=("SPX", "NDX"), exchange="CBOE"),
        qc_threshold=QcThresholdConfig(
            version="qc-1", max_spread_pct=0.05, max_quote_age_seconds=30.0, min_chain_count=5
        ),
        solver=SolverConfig(version="s-1", iv_tolerance=1e-8, max_iterations=100),
        surface=_surface("surf-1"),
        forward=_forward("fwd-1"),
        scenario=ScenarioConfig(
            version="sc-1", spot_shocks=(-0.1, 0.0, 0.1), vol_shocks=(-0.02, 0.02)
        ),
        monetization=MonetizationConfig(
            version="mon-1", gamma_normalisation="one_pct", theta_day_count=365
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


def test_section_versions_lists_every_section_stamp() -> None:
    assert section_versions(_config()) == {
        "universe": "u-1",
        "qc_threshold": "qc-1",
        "solver": "s-1",
        "surface": "surf-1",
        "forward": "fwd-1",
        "scenario": "sc-1",
        "monetization": "mon-1",
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
        " SolverConfig, SurfaceConfig, ForwardConfig, ScenarioConfig, MonetizationConfig,"
        " config_hash);"
        "print(config_hash(PlatformConfig("
        "universe=UniverseConfig(version='u-1', underlyings=('SPX','NDX'), exchange='CBOE'),"
        "qc_threshold=QcThresholdConfig(version='qc-1', max_spread_pct=0.05,"
        " max_quote_age_seconds=30.0, min_chain_count=5),"
        "solver=SolverConfig(version='s-1', iv_tolerance=1e-8, max_iterations=100),"
        "surface=SurfaceConfig(version='surf-1', svi_a_bounds=(0.0,10.0), svi_b_bounds=(1e-8,10.0),"
        " svi_rho_bounds=(-0.999,0.999), svi_m_bounds=(-5.0,5.0), svi_sigma_bounds=(1e-8,10.0),"
        " svi_bound_hit_tol=1e-5, svi_max_iterations=200),"
        "forward=ForwardConfig(version='fwd-1', good_rel_residual=1e-3, fair_rel_residual=1e-2,"
        " full_credit_pairs=4.0, rel_residual_halflife=1e-3, single_pair_confidence=0.30),"
        "scenario=ScenarioConfig(version='sc-1', spot_shocks=(-0.1,0.0,0.1),"
        " vol_shocks=(-0.02,0.02)),"
        "monetization=MonetizationConfig(version='mon-1', gamma_normalisation='one_pct',"
        " theta_day_count=365))))"
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
  tenor_grid: ["10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y"]
qc_threshold:
  version: qc-base
  max_spread_pct: 0.05
  max_quote_age_seconds: 30.0
  min_chain_count: 5
  grid:
    version: grid-base
    tenor_floors:
      "10d": 5
      "1m": 5
      "3m": 5
      "6m": 5
      "12m": 5
      "18m": 5
      "2y": 5
      "3y": 5
    band_low_delta: -0.30
    band_high_delta: 0.30
    max_delta_step: 0.25
solver:
  version: s-base
  iv_tolerance: 1.0e-8
  max_iterations: 100
  vol_min: 1.0e-9
  vol_max: 5.0
surface:
  version: surf-base
  svi_a_bounds: [0.0, 10.0]
  svi_b_bounds: [1.0e-8, 10.0]
  svi_rho_bounds: [-0.999, 0.999]
  svi_m_bounds: [-5.0, 5.0]
  svi_sigma_bounds: [1.0e-8, 10.0]
  svi_bound_hit_tol: 1.0e-5
  svi_max_iterations: 200
forward:
  version: fwd-base
  good_rel_residual: 1.0e-3
  fair_rel_residual: 1.0e-2
  full_credit_pairs: 4.0
  rel_residual_halflife: 1.0e-3
  single_pair_confidence: 0.30
scenario:
  version: sc-base
  spot_shocks: [-0.1, 0.0, 0.1]
  vol_shocks: [-0.02, 0.02]
  roll_down_days: [1]
  stress_surface:
    version: ss-base
    spot_shock_abs: 0.5
    vol_shock_abs: 0.5
    spot_steps: 9
    vol_steps: 9
monetization:
  version: mon-base
  gamma_normalisation: one_pct
  theta_day_count: 365
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
    incomplete.write_text(
        "universe:\n  version: u\n  underlyings: [SPX]\n  exchange: CBOE\n"
        '  tenor_grid: ["1m"]\n',
        "utf-8",
    )
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
    "universe.yaml": (
        "version: u-1\nunderlyings: [SPX, NDX]\nexchange: CBOE\n"
        'tenor_grid: ["10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y"]\n'
    ),
    "qc.yaml": (
        "version: qc-1\nmax_spread_pct: 0.05\nmax_quote_age_seconds: 30.0\nmin_chain_count: 5\n"
        "grid:\n  version: grid-qc-default\n  tenor_floors:\n"
        '    "10d": 5\n    "1m": 5\n    "3m": 5\n    "6m": 5\n'
        '    "12m": 5\n    "18m": 5\n    "2y": 5\n    "3y": 5\n'
        "  band_low_delta: -0.30\n  band_high_delta: 0.30\n  max_delta_step: 0.25\n"
    ),
    "pricing.yaml": (
        "solver:\n  version: s-1\n  iv_tolerance: 1.0e-8\n  max_iterations: 100\n"
        "  vol_min: 1.0e-9\n  vol_max: 5.0\n"
        "surface:\n  version: surf-1\n  svi_a_bounds: [0.0, 10.0]\n"
        "  svi_b_bounds: [1.0e-8, 10.0]\n  svi_rho_bounds: [-0.999, 0.999]\n"
        "  svi_m_bounds: [-5.0, 5.0]\n  svi_sigma_bounds: [1.0e-8, 10.0]\n"
        "  svi_bound_hit_tol: 1.0e-5\n  svi_max_iterations: 200\n"
        "forward:\n  version: fwd-1\n  good_rel_residual: 1.0e-3\n"
        "  fair_rel_residual: 1.0e-2\n  full_credit_pairs: 4.0\n"
        "  rel_residual_halflife: 1.0e-3\n  single_pair_confidence: 0.30\n"
    ),
    "scenarios.yaml": (
        "scenario:\n  version: sc-1\n  spot_shocks: [-0.1, 0.0, 0.1]\n"
        "  vol_shocks: [-0.02, 0.02]\n  roll_down_days: [1]\n"
        "  stress_surface:\n    version: ss-1\n    spot_shock_abs: 0.5\n"
        "    vol_shock_abs: 0.5\n    spot_steps: 9\n    vol_steps: 9\n"
        "monetization:\n  version: mon-1\n  gamma_normalisation: one_pct\n"
        "  theta_day_count: 365\n"
    ),
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
            surface=_surface("surf-1"),
            forward=_forward("fwd-1"),
            scenario=ScenarioConfig(
                version="sc-1",
                spot_shocks=(-0.1, 0.0, 0.1),
                vol_shocks=(-0.02, 0.02),
                stress_surface=StressSurfaceConfig(
                    version="ss-1",
                    spot_shock_abs=0.5,
                    vol_shock_abs=0.5,
                    spot_steps=9,
                    vol_steps=9,
                ),
            ),
            monetization=MonetizationConfig(
                version="mon-1", gamma_normalisation="one_pct", theta_day_count=365
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

    # ``grid`` is a nested-map-bearing field the flat reflective coercion cannot type, so it
    # is caller-supplied (the loader builds it via ``_build_qc_threshold``); these direct
    # build_dataclass calls exercise the scalar fields and let ``grid`` take its default.
    qc = build_dataclass(
        QcThresholdConfig, _GOOD_QC, section="qc_threshold",
        caller_supplied=frozenset({"grid"}),
    )
    assert qc.max_spread_pct == 0.05 and isinstance(qc.min_chain_count, int)
    # tuple[float, ...] coercion: a YAML list of numbers becomes a tuple of floats.
    # ``stress_surface`` is a nested dataclass field the loader builds via ``_build_scenario``,
    # so it is caller-supplied here (taking its default) like ``grid`` above.
    sc = build_dataclass(
        ScenarioConfig,
        {"version": "sc", "spot_shocks": [-0.1, 0, 0.1], "vol_shocks": [0.0], "roll_down_days": [1, 7]},
        section="scenario",
        caller_supplied=frozenset({"stress_surface"}),
    )
    assert sc.spot_shocks == (-0.1, 0.0, 0.1)
    assert all(isinstance(x, float) for x in sc.spot_shocks)
    # tuple[int, ...] coercion: a YAML list of day counts becomes a tuple of ints.
    assert sc.roll_down_days == (1, 7)
    assert all(isinstance(d, int) for d in sc.roll_down_days)


def test_build_dataclass_rejects_unknown_key() -> None:
    from algotrading.core.config import ConfigFieldError, build_dataclass

    with pytest.raises(ConfigFieldError) as exc:
        build_dataclass(
            QcThresholdConfig, {**_GOOD_QC, "typo": 1}, section="qc_threshold",
            caller_supplied=frozenset({"grid"}),
        )
    assert exc.value.field == "typo"


def test_build_dataclass_rejects_missing_field() -> None:
    from algotrading.core.config import ConfigFieldError, build_dataclass

    incomplete = {k: v for k, v in _GOOD_QC.items() if k != "min_chain_count"}
    with pytest.raises(ConfigFieldError) as exc:
        build_dataclass(
            QcThresholdConfig, incomplete, section="qc_threshold",
            caller_supplied=frozenset({"grid"}),
        )
    assert exc.value.field == "min_chain_count" and "missing" in exc.value.reason


def test_build_dataclass_rejects_fractional_int() -> None:
    from algotrading.core.config import ConfigFieldError, build_dataclass

    with pytest.raises(ConfigFieldError) as exc:
        build_dataclass(
            QcThresholdConfig, {**_GOOD_QC, "min_chain_count": 6.5}, section="qc_threshold",
            caller_supplied=frozenset({"grid"}),
        )
    assert exc.value.field == "min_chain_count"


def test_post_init_range_validation_raises_labelled_error() -> None:
    from algotrading.core.config import ConfigFieldError, build_dataclass

    with pytest.raises(ConfigFieldError) as exc:
        build_dataclass(
            QcThresholdConfig, {**_GOOD_QC, "max_spread_pct": -0.01}, section="qc_threshold",
            caller_supplied=frozenset({"grid"}),
        )
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


# -- per-bundle hashes + manifest freeze / validate_manifest (C7 increment 3a/4) -----

def _manifest(config: PlatformConfig, **overrides: object) -> Manifest:
    """A minimal Manifest frozen from a config — snapshot + per-bundle hashes."""
    kwargs: dict = dict(
        run_id="run-1",
        environment="test",
        code_version="1.0.0",
        config_hashes=config_hashes(config),
        config_snapshot=config_snapshot(config),
        input_partitions={},
        output_partitions={},
        status="ok",
    )
    kwargs.update(overrides)
    return Manifest(**kwargs)  # type: ignore[arg-type]


def test_config_hashes_are_per_bundle_and_move_only_their_bundle() -> None:
    # The blueprint manifest form: one hash per hashed Part VII bundle. An economic field
    # change moves exactly its bundle's hash and leaves the others byte-identical.
    import dataclasses

    base = _config()
    hashes = config_hashes(base)
    assert set(hashes) == {"universe", "qc", "pricing", "scenarios"}

    # Move a solver field — only the pricing bundle (which carries solver) changes.
    moved = dataclasses.replace(base, solver=dataclasses.replace(base.solver, iv_tolerance=1e-9))
    moved_hashes = config_hashes(moved)
    assert moved_hashes["pricing"] != hashes["pricing"]
    assert {k: moved_hashes[k] for k in ("universe", "qc", "scenarios")} == {
        k: hashes[k] for k in ("universe", "qc", "scenarios")
    }


def test_manifest_freeze_round_trips_and_validates() -> None:
    # A run replays from its manifest alone: the frozen snapshot rebuilds the same config,
    # and validate_manifest accepts a snapshot whose hashes match it.
    config = _config()
    manifest = _manifest(config)
    assert config_from_mapping(manifest.config_snapshot) == config
    validate_manifest(manifest)  # raises on mismatch; reaching here is the assertion


def test_validate_manifest_rejects_a_hash_that_disagrees_with_the_snapshot() -> None:
    # A tampered/stale snapshot cannot pass as a faithful freeze: the stored bundle hash
    # no longer equals a fresh recompute from the snapshot.
    config = _config()
    tampered = _manifest(config, config_hashes={**config_hashes(config), "pricing": "0" * 64})
    with pytest.raises(ManifestValidationError) as exc:
        validate_manifest(tampered)
    assert exc.value.bundle == "pricing"


def test_validate_manifest_accepts_a_snapshotless_manifest_with_hashes() -> None:
    # Older partitions (or a run that did not freeze a snapshot) validate as long as they
    # carry at least one bundle hash — the additive-nullable schema-evolution case.
    config = _config()
    manifest = _manifest(config, config_snapshot={})
    validate_manifest(manifest)
