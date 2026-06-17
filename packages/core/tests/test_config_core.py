from __future__ import annotations

import os
import subprocess
import sys

import pytest
from algotrading.core import (
    ForwardConfig,
    Manifest,
    ManifestValidationError,
    CurrencyRateConfig,
    MonetizationConfig,
    PlatformConfig,
    QcThresholdConfig,
    RatePillarConfig,
    RatesConfig,
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
        universe=UniverseConfig(version="u-1", exchange="CBOE"),
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


def test_config_hashes_are_byte_identical_to_the_pinned_oracle() -> None:
    config = _config()
    assert config_hash(config) == (
        "fc38f74127ff9bdbb6f51e144100a7832e668ebed743d2c729285085cf7c89bd"
    )
    # The pricing/qc/scenarios/universe bundle hashes stay byte-identical across the ADR-0054
    # rate-curve land: `rates` is its OWN bundle, so adding it moves only the whole-config
    # config_hash (a new section is present) and adds the `rates` key — no forward/analytics
    # golden moves on the rate curve's account.
    assert config_hashes(config) == {
        "pricing": "9083222ce26b63f5a935f8ad1667b5e0bcbb91c8cedb14b195941bdeeeb4b31e",
        "qc": "5ee4c4ee5fb3b4b07b94a00ad3d71277abec90bd3fc570b4ba1f643ca1238a12",
        "rates": "64e037b5a52f570f50003137a061f7e741c7805d4dfe695ac65ae48dfd8ec69f",
        "scenarios": "fc6d41e7a26e7ae36b80a8542118139082db9df572a82bb0a5e2945a06e392b8",
        "universe": "4833799bb76dcaaafeda85c23557159ab638407ca7122ac3d9796fd93d96e3e1",
    }


def test_supplementary_qc_cutoffs_fold_into_the_qc_bundle_hash() -> None:
    base = _config()
    hashes = config_hashes(base)
    for block, field, new_value in (
        ("continuity", "max_gap_count", 9),
        ("forward_engine", "max_rel_residual_mad", 0.007),
        ("fit_tolerance", "max_surface_rmse", 0.03),
        ("anomaly", "fail_z", 6.0),
        ("quote_integrity", "min_two_sided_fraction", 0.25),
        ("grid", "monitored_coverage_ratio", 0.90),
        ("grid", "calendar_abs_variance_tol", 0.001),
        ("grid", "calendar_rel_variance_tol", 0.10),
        ("grid", "ultra_short_maturity_years", 0.05),
    ):
        nested = getattr(base.qc_threshold, block).model_copy(update={field: new_value})
        moved = base.model_copy(
            update={"qc_threshold": base.qc_threshold.model_copy(update={block: nested})}
        )
        moved_hashes = config_hashes(moved)
        assert moved_hashes["qc"] != hashes["qc"], f"{block}.{field} must move the qc hash"
        assert {k: moved_hashes[k] for k in ("universe", "pricing", "scenarios")} == {
            k: hashes[k] for k in ("universe", "pricing", "scenarios")
        }


def test_anomaly_block_enforces_band_ordering() -> None:
    from algotrading.core.config import AnomalyQcConfig, ConfigFieldError

    with pytest.raises(ConfigFieldError):
        AnomalyQcConfig(version="bad", warn_z=5.0, fail_z=3.0)


def test_discovery_pool_size_folds_into_the_universe_hash() -> None:
    base = _config()
    hashes = config_hashes(base)
    bumped_strike = base.universe.strike_selection.model_copy(update={"discovery_pool_size": 12})
    moved = base.model_copy(
        update={"universe": base.universe.model_copy(update={"strike_selection": bumped_strike})}
    )
    moved_hashes = config_hashes(moved)
    assert moved_hashes["universe"] != hashes["universe"]
    assert {k: moved_hashes[k] for k in ("qc", "pricing", "scenarios")} == {
        k: hashes[k] for k in ("qc", "pricing", "scenarios")
    }


def test_quote_integrity_floor_enforces_a_fraction_range() -> None:
    from algotrading.core.config import ConfigFieldError, QuoteIntegrityQcConfig

    assert QuoteIntegrityQcConfig(version="qi", min_two_sided_fraction=0.0).min_two_sided_fraction == 0.0
    assert QuoteIntegrityQcConfig(version="qi", min_two_sided_fraction=1.0).min_two_sided_fraction == 1.0
    with pytest.raises(ConfigFieldError):
        QuoteIntegrityQcConfig(version="qi", min_two_sided_fraction=1.5)
    with pytest.raises(ConfigFieldError):
        QuoteIntegrityQcConfig(version="qi", min_two_sided_fraction=-0.1)


def _surface_with_grid(buckets: tuple[float, ...]) -> SurfaceConfig:
    return SurfaceConfig(
        version="surf-1",
        svi_a_bounds=(0.0, 10.0),
        svi_b_bounds=(1e-8, 10.0),
        svi_rho_bounds=(-0.999, 0.999),
        svi_m_bounds=(-5.0, 5.0),
        svi_sigma_bounds=(1e-8, 10.0),
        svi_bound_hit_tol=1e-5,
        svi_max_iterations=200,
        moneyness_buckets=buckets,
    )


def test_moneyness_buckets_default_is_the_canonical_symmetric_grid() -> None:
    assert _surface().moneyness_buckets == (-0.2, -0.1, 0.0, 0.1, 0.2)


@pytest.mark.parametrize(
    "bad",
    [
        (),
        (0.1, 0.0, -0.1),
        (-0.1, -0.1, 0.0, 0.1),
        (-0.2, -0.1, 0.1, 0.2),
        (-0.2, -0.1, 0.0, 0.1, 0.3),
    ],
)
def test_moneyness_buckets_reject_unordered_asymmetric_or_atm_less_grids(
    bad: tuple[float, ...],
) -> None:
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError):
        _surface_with_grid(bad)


def test_moneyness_buckets_folds_into_the_pricing_hash() -> None:
    base = _config()
    moved = base.model_copy(update={"surface": _surface_with_grid((-0.1, 0.0, 0.1))})
    base_h, moved_h = config_hashes(base), config_hashes(moved)
    assert moved_h["pricing"] != base_h["pricing"]
    assert {k: moved_h[k] for k in ("universe", "qc", "scenarios")} == {
        k: base_h[k] for k in ("universe", "qc", "scenarios")
    }


def test_lane3_reroute_knob_defaults_off_and_folds_into_the_pricing_hash() -> None:
    """ADR 0056: the railed-dense reroute ships OFF and is a hashed config behaviour, not a flip."""
    assert _surface().reroute_railed_dense_slice is False  # shipped default is OFF
    base = _config()
    flipped_surface = base.surface.model_copy(update={"reroute_railed_dense_slice": True})
    moved = base.model_copy(update={"surface": flipped_surface})
    base_h, moved_h = config_hashes(base), config_hashes(moved)
    assert moved_h["pricing"] != base_h["pricing"]  # flipping it moves the pricing hash
    assert {k: moved_h[k] for k in ("universe", "qc", "scenarios")} == {
        k: base_h[k] for k in ("universe", "qc", "scenarios")
    }


def test_surface_model_defaults_are_the_shipped_svi_with_nonparametric_fallback() -> None:
    surface = _surface()
    assert (surface.model, surface.fallback_model) == ("svi", "nonparametric")


@pytest.mark.parametrize(
    "field,bad",
    [
        ("model", "spline"),
        ("model", "nonparametric"),
        ("fallback_model", "spline"),
        ("fallback_model", "svi"),
    ],
)
def test_surface_model_rejects_unimplemented_methods(field: str, bad: str) -> None:
    from algotrading.core.config import ConfigFieldError

    kwargs = dict(
        version="surf-1",
        svi_a_bounds=(0.0, 10.0),
        svi_b_bounds=(1e-8, 10.0),
        svi_rho_bounds=(-0.999, 0.999),
        svi_m_bounds=(-5.0, 5.0),
        svi_sigma_bounds=(1e-8, 10.0),
        svi_bound_hit_tol=1e-5,
        svi_max_iterations=200,
    )
    kwargs[field] = bad
    with pytest.raises(ConfigFieldError):
        SurfaceConfig(**kwargs)  # type: ignore[arg-type]


def test_surface_model_choice_folds_into_the_pricing_hash() -> None:
    base = _config()
    alt_surface = base.surface.model_copy(update={"model": "svi-variant"})
    moved = base.model_copy(update={"surface": alt_surface})
    base_h, moved_h = config_hashes(base), config_hashes(moved)
    assert moved_h["pricing"] != base_h["pricing"]
    assert {k: moved_h[k] for k in ("universe", "qc", "scenarios")} == {
        k: base_h[k] for k in ("universe", "qc", "scenarios")
    }


def test_forward_engine_defaults_are_byte_identical_policy() -> None:
    fwd = _forward()
    assert (fwd.max_candidate_count, fwd.outlier_method, fwd.max_robust_zscore) == (
        None,
        "mad",
        3.5,
    )


@pytest.mark.parametrize(
    "update",
    [
        {"outlier_method": "iqr"},
        {"outlier_method": ""},
        {"max_candidate_count": 1},
        {"max_candidate_count": 0},
        {"max_robust_zscore": 0.0},
    ],
)
def test_forward_engine_rejects_bad_policy(update: dict[str, object]) -> None:
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError):
        ForwardConfig(
            version="fwd-bad",
            good_rel_residual=1e-3,
            fair_rel_residual=1e-2,
            full_credit_pairs=4.0,
            rel_residual_halflife=1e-3,
            single_pair_confidence=0.30,
            **update,  # type: ignore[arg-type]
        )


def test_forward_engine_max_candidate_count_none_is_allowed() -> None:
    assert _forward().model_copy(update={"max_candidate_count": None}).max_candidate_count is None


@pytest.mark.parametrize(
    "update",
    [
        {"max_candidate_count": 12},
        {"outlier_method": "none"},
        {"max_robust_zscore": 2.5},
    ],
)
def test_forward_engine_policy_folds_into_the_pricing_hash(update: dict[str, object]) -> None:
    base = _config()
    moved = base.model_copy(update={"forward": base.forward.model_copy(update=update)})
    base_h, moved_h = config_hashes(base), config_hashes(moved)
    assert moved_h["pricing"] != base_h["pricing"]
    assert {k: moved_h[k] for k in ("universe", "qc", "scenarios")} == {
        k: base_h[k] for k in ("universe", "qc", "scenarios")
    }


def test_config_hash_moves_when_any_field_moves() -> None:
    base = _config()
    moved = base.model_copy(
        update={"solver": base.solver.model_copy(update={"iv_tolerance": 1e-9})}
    )
    assert config_hash(moved) != config_hash(base)


def test_section_hash_isolates_one_section() -> None:
    base = _config()
    moved = base.model_copy(
        update={"scenario": base.scenario.model_copy(update={"vol_shocks": (-0.03, 0.03)})}
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
        "rates": "rates-default",
    }


def test_composite_config_hash_is_order_independent_and_sensitive() -> None:
    a = composite_config_hash({"qc": "h1", "forward": "h2"})
    b = composite_config_hash({"forward": "h2", "qc": "h1"})
    assert a == b
    assert composite_config_hash({"qc": "h1", "forward": "CHANGED"}) != a


def test_object_config_hash_is_sha256_of_the_canonical_json() -> None:
    import hashlib as _hashlib

    from algotrading.core.config import canonical_json, object_config_hash

    config = _config()
    expected = _hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()
    assert object_config_hash(config) == expected
    assert object_config_hash(config) == config_hash(config)
    assert object_config_hash(config.solver) == section_hash(config, "solver")


def test_composite_config_hash_matches_the_pinned_golden_digest() -> None:
    assert composite_config_hash({"qc": "h1", "forward": "h2"}) == (
        "606ebfa0c420f68c9b67af4e8c71fc0fd4883d5ee6e3f33c13c8f880cf00b294"
    )


def test_mapping_config_hash_matches_the_pinned_golden_digests() -> None:
    from pathlib import Path as _Path

    from algotrading.core.config import mapping_config_hash

    sample = {"b": [1, 2.5], "a": {"nested": True, "z": None}, "7": "seven", "neg": -0.0}
    assert mapping_config_hash(sample) == (
        "115ab1d4c9a08156a187e06bec3f63e14b8536b24d75198dc7a31791bb033bca"
    )
    assert mapping_config_hash({"path": _Path("/tmp/x"), "vals": (1, 2)}) == (
        "9dd57e323e263da3bac4928842134e78d12e9379012bcc665682069134aa51d5"
    )


def test_config_hash_is_stable_across_processes() -> None:
    expected = config_hash(_config())
    code = (
        "from algotrading.core import (PlatformConfig, UniverseConfig, QcThresholdConfig,"
        " SolverConfig, SurfaceConfig, ForwardConfig, ScenarioConfig, MonetizationConfig,"
        " config_hash);"
        "print(config_hash(PlatformConfig("
        "universe=UniverseConfig(version='u-1', exchange='CBOE'),"
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
    assert loaded.data["nested"]["y"] == 99
    assert load_yaml_config(overlay, base=base).config_hash == loaded.config_hash


_BASE_ECONOMIC_YAML = """\
universe:
  version: u-base
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
rates:
  version: rt-base
  currencies:
    EUR:
      currency: EUR
      source: estr_euribor_ois
      pillars:
        - { tenor_label: 3m, maturity_years: 0.25, instrument: euribor_3m }
        - { tenor_label: 1y, maturity_years: 1.0, instrument: euribor_12m }
"""


def test_from_config_builds_typed_platform_config_over_a_yaml_overlay(tmp_path) -> None:
    base = tmp_path / "base.yaml"
    overlay = tmp_path / "single_name.yaml"
    base.write_text(_BASE_ECONOMIC_YAML, encoding="utf-8")
    overlay.write_text(
        'universe:\n  tenor_grid: ["1m"]\nqc_threshold:\n  max_spread_pct: 0.02\n',
        encoding="utf-8",
    )

    config = from_config(load_yaml_config(overlay, base=base))

    assert isinstance(config, PlatformConfig)
    assert config.universe.tenor_grid == ("1m",)
    assert config.qc_threshold.max_spread_pct == 0.02
    assert config.universe.version == "u-base"
    assert config.universe.exchange == "CBOE"
    assert config.qc_threshold.max_quote_age_seconds == 30.0
    assert config.qc_threshold.min_chain_count == 5
    assert config.solver.iv_tolerance == 1e-8
    assert config.solver.max_iterations == 100
    assert config.scenario.spot_shocks == (-0.1, 0.0, 0.1)
    assert config.scenario.vol_shocks == (-0.02, 0.02)


def test_from_config_rejects_a_missing_section(tmp_path) -> None:
    from algotrading.core.config import ConfigError

    incomplete = tmp_path / "incomplete.yaml"
    incomplete.write_text(
        "universe:\n  version: u\n  exchange: CBOE\n"
        '  tenor_grid: ["1m"]\n',
        "utf-8",
    )
    with pytest.raises(ConfigError):
        from_config(load_yaml_config(incomplete))


def test_config_hash_collapses_signed_zero() -> None:
    base = _config()
    neg = base.model_copy(
        update={"scenario": base.scenario.model_copy(update={"vol_shocks": (-0.0, 0.05)})}
    )
    pos = base.model_copy(
        update={"scenario": base.scenario.model_copy(update={"vol_shocks": (0.0, 0.05)})}
    )
    assert config_hash(neg) == config_hash(pos)


_BUNDLES = {
    "universe.yaml": (
        "version: u-1\nexchange: CBOE\n"
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
    "rates.yaml": (
        "version: rt-1\ncurrencies:\n  EUR:\n    currency: EUR\n    source: estr_euribor_ois\n"
        "    pillars:\n"
        "      - { tenor_label: 3m, maturity_years: 0.25, instrument: euribor_3m }\n"
        "      - { tenor_label: 1y, maturity_years: 1.0, instrument: euribor_12m }\n"
    ),
}


def _write_bundles(configs_dir, *, extra: dict[str, str] | None = None) -> None:
    configs_dir.mkdir(parents=True, exist_ok=True)
    for name, text in {**_BUNDLES, **(extra or {})}.items():
        (configs_dir / name).write_text(text, encoding="utf-8")


def test_load_platform_config_assembles_the_six_bundles(tmp_path) -> None:
    from algotrading.core.config import load_platform_config

    _write_bundles(tmp_path)
    config = load_platform_config(tmp_path)

    assert isinstance(config, PlatformConfig)
    assert config.universe.exchange == "CBOE"
    assert config.qc_threshold.min_chain_count == 5
    assert config.solver.iv_tolerance == 1e-8
    assert config.scenario.spot_shocks == (-0.1, 0.0, 0.1)
    assert config_hash(config) == config_hash(
        PlatformConfig(
            universe=UniverseConfig(version="u-1", exchange="CBOE"),
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
            rates=RatesConfig(
                version="rt-1",
                currencies={
                    "EUR": CurrencyRateConfig(
                        currency="EUR",
                        source="estr_euribor_ois",
                        pillars=(
                            RatePillarConfig(
                                tenor_label="3m", maturity_years=0.25, instrument="euribor_3m"
                            ),
                            RatePillarConfig(
                                tenor_label="1y", maturity_years=1.0, instrument="euribor_12m"
                            ),
                        ),
                    )
                },
            ),
        )
    )


def test_load_platform_config_ignores_the_operational_bundles(tmp_path) -> None:
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
    from algotrading.core.config import ConfigError, load_platform_config

    _write_bundles(tmp_path)
    (tmp_path / "pricing.yaml").unlink()
    with pytest.raises(ConfigError, match="pricing.yaml"):
        load_platform_config(tmp_path)


def test_load_platform_config_loads_the_shipped_bundles() -> None:
    from pathlib import Path

    from algotrading.core.config import load_platform_config

    repo_root = Path(__file__).resolve().parents[3]
    config = load_platform_config(repo_root / "configs")
    assert config.universe.indices, "the shipped universe bundle must name the indices it tracks"
    assert isinstance(config_hash(config), str)
    signals = config.universe.signals
    assert signals.reference_tenor in config.universe.tenor_grid
    assert signals.term_slope_front in config.universe.tenor_grid
    assert signals.term_slope_back in config.universe.tenor_grid
    assert signals.term_slope_front != signals.term_slope_back


def test_signal_params_fold_into_only_the_universe_bundle_hash() -> None:
    base = _config()
    hashes = config_hashes(base)
    moved_signals = base.universe.signals.model_copy(update={"reference_tenor": "12m"})
    moved = base.model_copy(
        update={"universe": base.universe.model_copy(update={"signals": moved_signals})}
    )
    moved_hashes = config_hashes(moved)
    assert moved_hashes["universe"] != hashes["universe"]
    assert {k: moved_hashes[k] for k in ("qc", "pricing", "scenarios")} == {
        k: hashes[k] for k in ("qc", "pricing", "scenarios")
    }


def test_signal_entry_config_rejects_degenerate_params() -> None:
    from algotrading.core.config import ConfigFieldError, SignalEntryConfig

    with pytest.raises(ConfigFieldError, match="term_slope"):
        SignalEntryConfig(version="x", term_slope_front="3m", term_slope_back="3m")
    with pytest.raises(ConfigFieldError, match="basket_size"):
        SignalEntryConfig(version="x", basket_size=0)
    assert SignalEntryConfig(version="x", basket_size=None).basket_size is None


_DATED_PRICING = "effective_from: 2026-01-01\n" + _BUNDLES["pricing.yaml"]
_DATED_UNIVERSE = "effective_from: 2026-01-01\n" + _BUNDLES["universe.yaml"]


def test_load_platform_config_resolves_a_bundle_in_force_on_the_as_of(tmp_path) -> None:
    from datetime import date

    from algotrading.core.config import load_platform_config

    _write_bundles(tmp_path, extra={"pricing.yaml": _DATED_PRICING})
    dated = load_platform_config(tmp_path, as_of=date(2026, 6, 10))

    undated_dir = tmp_path / "undated"
    _write_bundles(undated_dir)
    undated = load_platform_config(undated_dir)

    assert config_hashes(dated) == config_hashes(undated)
    assert load_platform_config(tmp_path, as_of=date(2026, 1, 1)) is not None


def test_load_platform_config_rejects_config_authored_after_the_as_of(tmp_path) -> None:
    from datetime import date

    from algotrading.core.config import ConfigError, load_platform_config

    _write_bundles(tmp_path, extra={"pricing.yaml": _DATED_PRICING})
    with pytest.raises(ConfigError, match="pricing.yaml.*after the as_of"):
        load_platform_config(tmp_path, as_of=date(2025, 12, 31))


def test_load_platform_config_guards_a_whole_file_section_bundle(tmp_path) -> None:
    from datetime import date

    from algotrading.core.config import ConfigError, load_platform_config

    _write_bundles(tmp_path, extra={"universe.yaml": _DATED_UNIVERSE})
    assert load_platform_config(tmp_path, as_of=date(2026, 6, 10)) is not None
    with pytest.raises(ConfigError, match="universe.yaml.*after the as_of"):
        load_platform_config(tmp_path, as_of=date(2025, 1, 1))


def test_load_platform_config_without_as_of_ignores_effective_from(tmp_path) -> None:
    from algotrading.core.config import load_platform_config

    _write_bundles(tmp_path, extra={"pricing.yaml": _DATED_PRICING})
    assert load_platform_config(tmp_path) is not None


def test_load_platform_config_rejects_a_malformed_effective_from(tmp_path) -> None:
    from datetime import date

    from algotrading.core.config import ConfigError, load_platform_config

    bad_pricing = 'effective_from: "not-a-date"\n' + _BUNDLES["pricing.yaml"]
    _write_bundles(tmp_path, extra={"pricing.yaml": bad_pricing})
    with pytest.raises(ConfigError, match="pricing.yaml.*malformed effective_from"):
        load_platform_config(tmp_path, as_of=date(2026, 6, 10))


def test_mapping_hash_collapses_signed_zero() -> None:
    from algotrading.core.config import mapping_config_hash

    assert mapping_config_hash({"shock": -0.0}) == mapping_config_hash({"shock": 0.0})


def test_canonical_json_and_mapping_hash_reject_non_finite() -> None:
    from algotrading.core.config import canonical_json, mapping_config_hash

    with pytest.raises(ValueError):
        canonical_json([float("nan")])
    with pytest.raises(ValueError):
        canonical_json([float("inf")])
    with pytest.raises(ValueError):
        mapping_config_hash({"x": float("nan")})
    with pytest.raises(ValueError):
        mapping_config_hash({"x": float("-inf")})


_GOOD_GRID = {
    "version": "grid-qc-1",
    "tenor_floors": {"10d": 5, "1m": 5, "3m": 5, "6m": 5, "12m": 5, "18m": 5, "2y": 5, "3y": 5},
    "band_low_delta": -0.30,
    "band_high_delta": 0.30,
    "max_delta_step": 0.25,
}
_GOOD_QC = {
    "version": "qc-1",
    "max_spread_pct": 0.05,
    "max_quote_age_seconds": 30.0,
    "min_chain_count": 6,
    "grid": _GOOD_GRID,
}


def _build_qc(mapping: dict) -> QcThresholdConfig:
    from algotrading.core.config.loader import _build_section

    return _build_section(QcThresholdConfig, "qc_threshold", mapping)


def test_section_model_coerces_by_declared_type() -> None:
    qc = _build_qc(_GOOD_QC)
    assert qc.max_spread_pct == 0.05 and isinstance(qc.min_chain_count, int)
    assert qc.grid.tenor_floors["10d"] == 5

    sc = ScenarioConfig(
        version="sc", spot_shocks=[-0.1, 0.0, 0.1], vol_shocks=[0.0], roll_down_days=[1, 7]
    )
    assert sc.spot_shocks == (-0.1, 0.0, 0.1)
    assert all(isinstance(x, float) for x in sc.spot_shocks)
    assert sc.roll_down_days == (1, 7)
    assert all(isinstance(d, int) for d in sc.roll_down_days)


def test_scenario_config_named_and_correlation_families_validate_and_default_empty() -> None:
    from algotrading.core.config import NamedScenarioConfig

    bare = ScenarioConfig(version="sc", spot_shocks=[-0.1], vol_shocks=[0.0])
    assert bare.named_scenarios == ()
    assert bare.correlation_shocks == ()

    sc = ScenarioConfig(
        version="sc",
        spot_shocks=[-0.1],
        vol_shocks=[0.0],
        correlation_shocks=[0.10, 0.20],
        named_scenarios=[
            {"label": "2008", "spot_shock": -0.45, "vol_shock": 0.40, "rate_shock": -0.02},
        ],
    )
    assert sc.correlation_shocks == (0.10, 0.20)
    assert len(sc.named_scenarios) == 1
    named = sc.named_scenarios[0]
    assert isinstance(named, NamedScenarioConfig)
    assert (named.label, named.spot_shock, named.vol_shock, named.rate_shock) == (
        "2008",
        -0.45,
        0.40,
        -0.02,
    )
    assert named.correlation_shock == 0.0


def test_scenario_config_rejects_duplicate_named_scenario_labels() -> None:
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError, match="labels must be unique"):
        ScenarioConfig(
            version="sc",
            spot_shocks=[-0.1],
            vol_shocks=[0.0],
            named_scenarios=[
                {"label": "2008", "spot_shock": -0.45},
                {"label": "2008", "spot_shock": -0.30},
            ],
        )


def test_named_and_correlation_families_fold_into_only_the_scenarios_bundle_hash() -> None:
    base = _config()
    moved = base.model_copy(
        update={
            "scenario": base.scenario.model_copy(
                update={
                    "correlation_shocks": (0.10,),
                    "named_scenarios": (
                        {"label": "2008", "spot_shock": -0.45},
                    ),
                }
            )
        }
    )
    base_h = config_hashes(base)
    moved_h = config_hashes(moved)
    assert moved_h["scenarios"] != base_h["scenarios"]
    assert {k: moved_h[k] for k in ("universe", "qc", "pricing")} == {
        k: base_h[k] for k in ("universe", "qc", "pricing")
    }


def test_section_model_rejects_unknown_key() -> None:
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError) as exc:
        _build_qc({**_GOOD_QC, "typo": 1})
    assert exc.value.field == "typo"


def test_section_model_rejects_missing_field() -> None:
    from algotrading.core.config import ConfigFieldError

    incomplete = {k: v for k, v in _GOOD_QC.items() if k != "min_chain_count"}
    with pytest.raises(ConfigFieldError) as exc:
        _build_qc(incomplete)
    assert exc.value.field == "min_chain_count"


def test_section_model_rejects_fractional_int() -> None:
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError) as exc:
        _build_qc({**_GOOD_QC, "min_chain_count": 6.5})
    assert exc.value.field == "min_chain_count"


def test_section_model_rejects_bool_as_int() -> None:
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError) as exc:
        _build_qc({**_GOOD_QC, "min_chain_count": True})
    assert exc.value.field == "min_chain_count"


def test_range_validation_raises_labelled_error() -> None:
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError) as exc:
        _build_qc({**_GOOD_QC, "max_spread_pct": -0.01})
    assert exc.value.section == "qc_threshold"
    assert exc.value.field == "max_spread_pct"
    assert exc.value.value == -0.01


def test_from_config_surfaces_a_bad_economic_value(tmp_path) -> None:
    from algotrading.core.config import ConfigFieldError

    bad = tmp_path / "bad.yaml"
    base = tmp_path / "base.yaml"
    base.write_text(_BASE_ECONOMIC_YAML, encoding="utf-8")
    bad.write_text("qc_threshold:\n  min_chain_count: 0\n", encoding="utf-8")
    with pytest.raises(ConfigFieldError) as exc:
        from_config(load_yaml_config(bad, base=base))
    assert exc.value.section == "qc_threshold" and exc.value.field == "min_chain_count"


def _manifest(config: PlatformConfig, **overrides: object) -> Manifest:
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
    base = _config()
    hashes = config_hashes(base)
    assert set(hashes) == {"universe", "qc", "pricing", "scenarios", "rates"}

    moved = base.model_copy(
        update={"solver": base.solver.model_copy(update={"iv_tolerance": 1e-9})}
    )
    moved_hashes = config_hashes(moved)
    assert moved_hashes["pricing"] != hashes["pricing"]
    assert {k: moved_hashes[k] for k in ("universe", "qc", "scenarios", "rates")} == {
        k: hashes[k] for k in ("universe", "qc", "scenarios", "rates")
    }


def test_manifest_freeze_round_trips_and_validates() -> None:
    config = _config()
    manifest = _manifest(config)
    assert config_from_mapping(manifest.config_snapshot) == config
    validate_manifest(manifest)


def test_validate_manifest_rejects_a_hash_that_disagrees_with_the_snapshot() -> None:
    config = _config()
    tampered = _manifest(config, config_hashes={**config_hashes(config), "pricing": "0" * 64})
    with pytest.raises(ManifestValidationError) as exc:
        validate_manifest(tampered)
    assert exc.value.bundle == "pricing"


def test_validate_manifest_accepts_a_snapshotless_manifest_with_hashes() -> None:
    config = _config()
    manifest = _manifest(config, config_snapshot={})
    validate_manifest(manifest)
