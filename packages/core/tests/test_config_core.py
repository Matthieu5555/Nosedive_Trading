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
    # REP6 acceptance bar: the pydantic v2 config layer must produce the *same* bytes the
    # hand-rolled dataclass layer did. These SHA-256 values were captured from the committed
    # dataclass-era code (the golden oracle) over the fixed ``_config()`` bundle; the pydantic
    # rewrite must reproduce them exactly. A changed hash is a reproducibility break, not a
    # migration — every historical record branded with one of these must still resolve.
    #
    # C7 increment 2 (ADR 0028): the supplementary QC cut-offs and the anomaly bands moved
    # from `.py` literals into the hashed `qc` block (QcThresholdConfig.continuity /
    # forward_engine / fit_tolerance / anomaly). The `qc` bundle hash — and so the folded
    # whole-config hash — therefore moved BY DESIGN; the three other bundle hashes
    # (universe / pricing / scenarios) are byte-identical to the pre-expansion oracle, which
    # is the section-isolation guarantee. The qc/full values below are regenerated over the
    # expanded `qc` bundle.
    #
    # T-delta-window (2026-06-12, ADR 0028): `StrikeSelectionConfig.discovery_working_vol` joined
    # the hashed `universe` bundle (the conservative vol that sizes the discovery strike window;
    # it lives in config, not a `.py` literal). The `universe` bundle hash — and so the folded
    # whole-config hash — moved BY DESIGN; `qc`/`pricing`/`scenarios` stay byte-identical (section
    # isolation). The universe/full values below are regenerated over the expanded `universe`
    # bundle. This is a pre-capture dev change: no banked historical record carries the old hash.
    #
    # T-qc-residual-units (2026-06-12, ADR 0028): the forward/parity QC cut-offs moved from
    # absolute-$ (`max_residual_mad`/`max_parity_residual`) to relative-to-forward
    # (`max_rel_residual_mad`/`max_rel_parity_residual`) — an absolute $ bar was an always-FAIL
    # false positive on a 7400-pt index. The field rename + new defaults + version bump moved the
    # `qc` bundle hash — and so the folded whole-config hash — BY DESIGN; `universe`/`pricing`/
    # `scenarios` stay byte-identical (section isolation). Pre-capture dev change: no banked record
    # carries the old hash.
    #
    # T-index-only-refactor (2026-06-13): the stale `underlyings` list was removed from
    # `UniverseConfig` (one universe source = the index registry; single-names are index
    # constituents, never option underlyings). Dropping the field moved the `universe` bundle
    # hash — and so the folded whole-config hash — BY DESIGN; `qc`/`pricing`/`scenarios` stay
    # byte-identical (section isolation). Pre-capture dev change: no banked record carries the old hash.
    #
    # T-delta-step-2 (2026-06-13, ADR 0028): the projection's delta-band *emission* step joined
    # the hashed `qc` block as `GridQcConfig.band_step` (the grid the projection emits and the grid
    # this plane validates now read one band definition — they cannot drift), and `qc.yaml`
    # tightened `max_delta_step` 0.25→0.02 so the QC forces the prof's ±30Δ pas-2 grid. The `qc`
    # bundle hash — and so the folded whole-config hash — moved BY DESIGN; `universe`/`pricing`/
    # `scenarios` stay byte-identical (section isolation). Pre-capture dev change: no banked record
    # carries the old hash.
    #
    # T-scenario-rate-axis (2026-06-13): `scenarios.yaml` gained the course's third stress axis —
    # `ScenarioConfig.rate_shocks` (additive rate moves, ±25 bp) — and the section `version` bumped
    # to 2026.06.13. The `scenarios` bundle hash — and so the folded whole-config hash — moved BY
    # DESIGN; `universe`/`qc`/`pricing` stay byte-identical (section isolation). Pre-capture dev
    # change: no banked record carries the old hash.
    #
    # T-explicit-rate-parameter (2026-06-13): `ForwardConfig.rate` joined the hashed `forward`
    # block in `pricing.yaml` — the blueprint's explicit interest-rate *input* to the carry/
    # dividend split (Eq 5), `null` by default (= the prior parity-DF-implied rate, so computed
    # values are unchanged). The `pricing` bundle hash — and so the folded whole-config hash —
    # moved BY DESIGN; `universe`/`qc`/`scenarios` stay byte-identical (section isolation).
    # Pre-capture dev change: no banked record carries the old hash.
    #
    # T-pricing-config-completeness (2026-06-13): `SurfaceConfig.min_points_per_slice` joined the
    # hashed `surface` block in `pricing.yaml` — the SVI-trust routing threshold gets a typed home
    # instead of the `MIN_POINTS_FOR_SVI` .py literal (ADR 0028). Default 5 = unchanged routing.
    # The `pricing` bundle hash — and so the folded whole-config hash — moved BY DESIGN;
    # `universe`/`qc`/`scenarios` stay byte-identical (section isolation). Pre-capture dev change.
    #
    # infra-sx5e-weighted-membership (2026-06-14, ADR 0044): `UniverseConfig.dispersion_top_n`
    # joined the hashed `universe` bundle (the S1 dispersion-basket size the `top_n_by_weight`
    # selector returns; it lives in config, not a `.py` literal). ibkr-constituent-option-capture
    # (2026-06-14, ADR 0045) likewise added `UniverseConfig.constituent_top_n`. Both move ONLY the
    # `universe` bundle hash — and so the folded whole-config hash — BY DESIGN; `qc`/`pricing`/
    # `scenarios` stay byte-identical (section isolation). The universe/full values below are
    # regenerated over the expanded `universe` bundle. Pre-capture dev change: no banked record
    # carries the old hash.
    #
    # core-projection-moneyness-grid (2026-06-14, ADR 0028): `SurfaceConfig.moneyness_buckets` joined
    # the hashed `surface` block in `pricing.yaml` — the surface-projection log-moneyness grid gets a
    # typed home instead of the `DEFAULT_MONEYNESS_BUCKETS` .py literal. Default
    # [-0.2,-0.1,0,0.1,0.2] = byte-identical projection. The `pricing` bundle hash — and so the folded
    # whole-config hash — moved BY DESIGN; `universe`/`qc`/`scenarios` stay byte-identical (section
    # isolation). Pre-capture dev change: no banked record carries the old hash.
    config = _config()
    assert config_hash(config) == (
        "0c39029dc276fa39a5b3c0ffd3c73cf337d9b645d5fa20d354b3d81f56b8f019"
    )
    assert config_hashes(config) == {
        "pricing": "c78c40d6cc0a7970dab5d1c24d621af87155b15476780a89b2ebff8188dc6850",
        "qc": "7f2ceefa49887917c400092795cfffb8723bc6bbf752aa51bacd90de8c941b3f",
        "scenarios": "41dffc62f417d57b7efb800fa4dd3b0cdf4a1d1d7b6aea1fef429e7f77d19e4d",
        "universe": "a557c26b5d97d8a077c02406e3ec4ce783fcb0fc20c7e9956e771d875087ce97",
    }


def test_supplementary_qc_cutoffs_fold_into_the_qc_bundle_hash() -> None:
    # C7 increment 2 (ADR 0028): the supplementary QC cut-offs and anomaly bands live in the
    # hashed `qc` block, not `.py` literals — so moving one moves ONLY the `qc` bundle hash,
    # leaving the other three byte-identical (section isolation). Independent oracle: bump a
    # cut-off in each new nested block and assert the qc hash changes while pricing/universe/
    # scenarios do not.
    base = _config()
    hashes = config_hashes(base)
    for block, field, new_value in (
        ("continuity", "max_gap_count", 9),
        ("forward_engine", "max_rel_residual_mad", 0.007),
        ("fit_tolerance", "max_surface_rmse", 0.03),
        ("anomaly", "fail_z", 6.0),
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
    # The anomaly block carries the rolling-baseline robust-z bands; a mis-tuned config
    # (fail_z below warn_z) is rejected at construction, never silently accepted.
    from algotrading.core.config import AnomalyQcConfig, ConfigFieldError

    with pytest.raises(ConfigFieldError):
        AnomalyQcConfig(version="bad", warn_z=5.0, fail_z=3.0)


def _surface_with_grid(buckets: tuple[float, ...]) -> SurfaceConfig:
    """A SurfaceConfig with the standard bounds and a caller-chosen moneyness grid.

    Built through the constructor (not ``model_copy``/``model_validate``) so the after-validator
    runs and a bad grid surfaces as the labelled ``ConfigFieldError`` the loader raises too.
    """
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
    # The shipped default (no override) is the ATM-centered ±0.1/±0.2 log-moneyness grid.
    assert _surface().moneyness_buckets == (-0.2, -0.1, 0.0, 0.1, 0.2)


@pytest.mark.parametrize(
    "bad",
    [
        (),  # empty
        (0.1, 0.0, -0.1),  # not strictly increasing (descending)
        (-0.1, -0.1, 0.0, 0.1),  # duplicate → not strictly increasing
        (-0.2, -0.1, 0.1, 0.2),  # missing the ATM 0.0 point
        (-0.2, -0.1, 0.0, 0.1, 0.3),  # not symmetric about 0.0
    ],
)
def test_moneyness_buckets_reject_unordered_asymmetric_or_atm_less_grids(
    bad: tuple[float, ...],
) -> None:
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError):
        _surface_with_grid(bad)


def test_moneyness_buckets_folds_into_the_pricing_hash() -> None:
    # Changing the grid moves ONLY the pricing bundle hash (section isolation), proving it is a
    # hashed economic input — the drift the ADR-0028 audit class exists to flag.
    base = _config()
    moved = base.model_copy(update={"surface": _surface_with_grid((-0.1, 0.0, 0.1))})
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
    # Bump only the scenario grid: scenario hash moves, solver hash does not.
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
    }


def test_composite_config_hash_is_order_independent_and_sensitive() -> None:
    a = composite_config_hash({"qc": "h1", "forward": "h2"})
    b = composite_config_hash({"forward": "h2", "qc": "h1"})
    assert a == b
    assert composite_config_hash({"qc": "h1", "forward": "CHANGED"}) != a


def test_object_config_hash_is_sha256_of_the_canonical_json() -> None:
    # The single config-object hasher (M14): independently recomputed here as
    # hashlib-over-canonical_json, and it must agree with config_hash on a whole
    # PlatformConfig (config_hash is the same operation, typed narrower).
    import hashlib as _hashlib

    from algotrading.core.config import canonical_json, object_config_hash

    config = _config()
    expected = _hashlib.sha256(canonical_json(config).encode("utf-8")).hexdigest()
    assert object_config_hash(config) == expected
    assert object_config_hash(config) == config_hash(config)
    assert object_config_hash(config.solver) == section_hash(config, "solver")


def test_composite_config_hash_matches_the_pinned_golden_digest() -> None:
    # Golden-hash pin (M25): captured from the committed pre-`core.hashing` code
    # (audit-fixes-batch1, 2026-06-12). Freezes the bare canonical-JSON + SHA-256
    # convention this helper uses, so routing it through `core.hashing` is provably
    # hash-neutral. If this moves, revert — never regenerate.
    assert composite_config_hash({"qc": "h1", "forward": "h2"}) == (
        "606ebfa0c420f68c9b67af4e8c71fc0fd4883d5ee6e3f33c13c8f880cf00b294"
    )


def test_mapping_config_hash_matches_the_pinned_golden_digests() -> None:
    # Golden-hash pins (M25): captured from the committed pre-`core.hashing` code
    # (audit-fixes-batch1, 2026-06-12). They freeze the *yaml-loader* convention —
    # stringified keys, ``default=str``, ``allow_nan=False`` — which deliberately
    # differs from the bare convention (`core.hashing.canonical_dumps`) and from the
    # typed-config convention (`canonical_json`). If one moves, revert.
    from pathlib import Path as _Path

    from algotrading.core.config import mapping_config_hash

    sample = {"b": [1, 2.5], "a": {"nested": True, "z": None}, "7": "seven", "neg": -0.0}
    assert mapping_config_hash(sample) == (
        "115ab1d4c9a08156a187e06bec3f63e14b8536b24d75198dc7a31791bb033bca"
    )
    # default=str: a non-JSON value (a Path) is stringified, not a crash.
    assert mapping_config_hash({"path": _Path("/tmp/x"), "vals": (1, 2)}) == (
        "9dd57e323e263da3bac4928842134e78d12e9379012bcc665682069134aa51d5"
    )


def test_config_hash_is_stable_across_processes() -> None:
    # No PYTHONHASHSEED dependence (TESTING.md cross-process requirement).
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
    assert loaded.data["nested"]["y"] == 99  # overlay wins
    # Re-loading the same files yields the same content hash.
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
        'universe:\n  tenor_grid: ["1m"]\nqc_threshold:\n  max_spread_pct: 0.02\n',
        encoding="utf-8",
    )

    config = from_config(load_yaml_config(overlay, base=base))

    assert isinstance(config, PlatformConfig)
    # List value replaced wholesale by the overlay (not the base 8-tenor grid), as a tuple.
    assert config.universe.tenor_grid == ("1m",)
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
        "universe:\n  version: u\n  exchange: CBOE\n"
        '  tenor_grid: ["1m"]\n',
        "utf-8",
    )
    with pytest.raises(ConfigError):
        from_config(load_yaml_config(incomplete))


def test_config_hash_collapses_signed_zero() -> None:
    # -0.0 and 0.0 are mathematically equal; a reproducibility hash must not split them
    # (they serialize to different JSON tokens without normalization).
    base = _config()
    neg = base.model_copy(
        update={"scenario": base.scenario.model_copy(update={"vol_shocks": (-0.0, 0.05)})}
    )
    pos = base.model_copy(
        update={"scenario": base.scenario.model_copy(update={"vol_shocks": (0.0, 0.05)})}
    )
    assert config_hash(neg) == config_hash(pos)


# -- load_platform_config: the six-bundle Part VII loader (C7 increment 1) -----------

# The four economic bundle files, each authored as one file per the blueprint Part VII
# taxonomy. environment.yaml + broker.yaml are operational and must NOT be loaded into
# the hashed typed config — the loader ignores them.
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
    assert config.universe.exchange == "CBOE"                   # universe.yaml
    assert config.qc_threshold.min_chain_count == 5             # qc.yaml
    assert config.solver.iv_tolerance == 1e-8                   # pricing.yaml → solver section
    assert config.scenario.spot_shocks == (-0.1, 0.0, 0.1)     # scenarios.yaml
    # Hashable and equal to the same config built directly: layout does not change content.
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
    # The shipped universe bundle names the indices it tracks (the single universe source —
    # there is no separate underlyings list anymore, T-index-only-refactor).
    assert config.universe.indices, "the shipped universe bundle must name the indices it tracks"
    assert isinstance(config_hash(config), str)


# -- as-of / effective-dated config resolution (core-config-effective-dating, ADR 0028) -

# A bundle dates itself with a top-level `effective_from:` beside its section blocks. The
# loader pops it (the section models forbid an unknown key) and uses it only to guard a
# replay against look-ahead. Dating pricing.yaml exercises the multi-section file; dating
# universe.yaml exercises the whole-file (`subkey is None`) section.
_DATED_PRICING = "effective_from: 2026-01-01\n" + _BUNDLES["pricing.yaml"]
_DATED_UNIVERSE = "effective_from: 2026-01-01\n" + _BUNDLES["universe.yaml"]


def test_load_platform_config_resolves_a_bundle_in_force_on_the_as_of(tmp_path) -> None:
    # A replay whose as_of is on/after a bundle's effective_from resolves it — the config
    # WAS in force then. And effective_from is metadata, not economics: the resolved config
    # is byte-identical (same per-bundle hashes) to the same bundles without the dating key,
    # so dating a bundle never moves a reproducibility hash.
    from datetime import date

    from algotrading.core.config import load_platform_config

    _write_bundles(tmp_path, extra={"pricing.yaml": _DATED_PRICING})
    dated = load_platform_config(tmp_path, as_of=date(2026, 6, 10))

    undated_dir = tmp_path / "undated"
    _write_bundles(undated_dir)
    undated = load_platform_config(undated_dir)

    assert config_hashes(dated) == config_hashes(undated)
    # The boundary day (as_of == effective_from) is "in force", not look-ahead.
    assert load_platform_config(tmp_path, as_of=date(2026, 1, 1)) is not None


def test_load_platform_config_rejects_config_authored_after_the_as_of(tmp_path) -> None:
    # The look-ahead guard: replaying a day before a bundle's effective_from must fail —
    # that bundle did not exist on the replayed day, so using it would leak future config.
    from datetime import date

    from algotrading.core.config import ConfigError, load_platform_config

    _write_bundles(tmp_path, extra={"pricing.yaml": _DATED_PRICING})
    with pytest.raises(ConfigError, match="pricing.yaml.*after the as_of"):
        load_platform_config(tmp_path, as_of=date(2025, 12, 31))


def test_load_platform_config_guards_a_whole_file_section_bundle(tmp_path) -> None:
    # universe.yaml is a `subkey is None` bundle (the whole file root is the section), so
    # its effective_from must be popped before UniverseConfig (extra="forbid") sees it —
    # both that it loads clean when in force, and that the guard still fires when not.
    from datetime import date

    from algotrading.core.config import ConfigError, load_platform_config

    _write_bundles(tmp_path, extra={"universe.yaml": _DATED_UNIVERSE})
    assert load_platform_config(tmp_path, as_of=date(2026, 6, 10)) is not None
    with pytest.raises(ConfigError, match="universe.yaml.*after the as_of"):
        load_platform_config(tmp_path, as_of=date(2025, 1, 1))


def test_load_platform_config_without_as_of_ignores_effective_from(tmp_path) -> None:
    # Absence of as_of is the live "current" path: a dated bundle loads exactly as an
    # undated one, never guarded — the zero-churn default for every existing caller.
    from algotrading.core.config import load_platform_config

    _write_bundles(tmp_path, extra={"pricing.yaml": _DATED_PRICING})
    assert load_platform_config(tmp_path) is not None


def test_load_platform_config_rejects_a_malformed_effective_from(tmp_path) -> None:
    # A dating key that is not an ISO date fails loudly naming the bundle — never a silent
    # default (the ADR-0028 discipline applies to the dating metadata too).
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


# A complete, valid qc_threshold section mapping (scalar cut-offs + the nested grid block),
# the shape the loader validates into QcThresholdConfig. Each rejection test below mutates a
# single field of a copy so the failure is isolated to that one bad value.
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
    """Validate a qc_threshold mapping through the loader's pydantic seam.

    ``_build_section`` is the one error boundary that maps a pydantic ``ValidationError``
    onto the structured ``ConfigFieldError(section, field, value, reason)`` callers depend on.
    """
    from algotrading.core.config.loader import _build_section

    return _build_section(QcThresholdConfig, "qc_threshold", mapping)


def test_section_model_coerces_by_declared_type() -> None:
    # The pydantic section models are the validation seam (REP6): nested ``grid`` is a native
    # nested model, a YAML list becomes the declared tuple, and strict typing is preserved.
    qc = _build_qc(_GOOD_QC)
    assert qc.max_spread_pct == 0.05 and isinstance(qc.min_chain_count, int)
    assert qc.grid.tenor_floors["10d"] == 5  # nested dict[str, int] field

    # tuple[float, ...] / tuple[int, ...]: a YAML list becomes the declared tuple type.
    sc = ScenarioConfig(
        version="sc", spot_shocks=[-0.1, 0.0, 0.1], vol_shocks=[0.0], roll_down_days=[1, 7]
    )
    assert sc.spot_shocks == (-0.1, 0.0, 0.1)
    assert all(isinstance(x, float) for x in sc.spot_shocks)
    assert sc.roll_down_days == (1, 7)
    assert all(isinstance(d, int) for d in sc.roll_down_days)


def test_section_model_rejects_unknown_key() -> None:
    # extra="forbid": an unknown YAML key is rejected, naming the field.
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError) as exc:
        _build_qc({**_GOOD_QC, "typo": 1})
    assert exc.value.field == "typo"


def test_section_model_rejects_missing_field() -> None:
    # A required economic field absent from the mapping is rejected, naming the field —
    # never a silent default.
    from algotrading.core.config import ConfigFieldError

    incomplete = {k: v for k, v in _GOOD_QC.items() if k != "min_chain_count"}
    with pytest.raises(ConfigFieldError) as exc:
        _build_qc(incomplete)
    assert exc.value.field == "min_chain_count"


def test_section_model_rejects_fractional_int() -> None:
    # strict=True: 10.5 for an int field is a config error, not a silent truncation.
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError) as exc:
        _build_qc({**_GOOD_QC, "min_chain_count": 6.5})
    assert exc.value.field == "min_chain_count"


def test_section_model_rejects_bool_as_int() -> None:
    # strict=True: a bool is not an int (a bool where an int is declared is a mistake).
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError) as exc:
        _build_qc({**_GOOD_QC, "min_chain_count": True})
    assert exc.value.field == "min_chain_count"


def test_range_validation_raises_labelled_error() -> None:
    # A Field(gt=0) range violation raises the structured error naming section/field/value.
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError) as exc:
        _build_qc({**_GOOD_QC, "max_spread_pct": -0.01})
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
    base = _config()
    hashes = config_hashes(base)
    assert set(hashes) == {"universe", "qc", "pricing", "scenarios"}

    # Move a solver field — only the pricing bundle (which carries solver) changes.
    moved = base.model_copy(
        update={"solver": base.solver.model_copy(update={"iv_tolerance": 1e-9})}
    )
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
