"""The shared grid-identity helpers (M44): ordered de-dup + the short construction hash.

The hash expectations are derived independently of the code under test: the canonical
JSON strings are spelled out as literals (sorted keys, compact separators — the documented
encoding) and hashed here with hashlib, then compared to the helper's output. The
byte-identity pins below also freeze the persisted ``effective_*_version`` strings the
inline copies produced before the consolidation, so the M44 move is provably hash-neutral.
"""

from __future__ import annotations

import hashlib

from algotrading.infra.risk.grid_versioning import (
    dedup_preserving_order,
    short_construction_hash,
)
from algotrading.infra.risk.scenarios import _grid_construction_hash
from algotrading.infra.risk.stress_surface import _surface_construction_hash


# --- dedup_preserving_order ---------------------------------------------------------
def test_dedup_keeps_first_seen_order() -> None:
    assert dedup_preserving_order((0.1, -0.1, 0.1, 0.0, -0.1)) == (0.1, -0.1, 0.0)


def test_dedup_is_identity_on_distinct_values() -> None:
    assert dedup_preserving_order((-0.2, 0.0, 0.2)) == (-0.2, 0.0, 0.2)


def test_dedup_of_empty_is_empty() -> None:
    assert dedup_preserving_order(()) == ()


def test_dedup_collapses_an_all_equal_axis_to_one_point() -> None:
    assert dedup_preserving_order((0.0, 0.0, 0.0)) == (0.0,)


# --- short_construction_hash --------------------------------------------------------
def test_short_hash_matches_hand_built_canonical_json() -> None:
    # Independent oracle: the canonical form of {"b": 2, "a": 1} under sorted keys and
    # (",", ":") separators is exactly this literal; the helper must hash these bytes.
    expected = hashlib.sha256(b'{"a":1,"b":2}').hexdigest()[:12]
    assert short_construction_hash({"b": 2, "a": 1}) == expected


def test_short_hash_is_12_hex_chars_by_default_and_length_parametrizable() -> None:
    full = hashlib.sha256(b'{"k":"v"}').hexdigest()
    assert short_construction_hash({"k": "v"}) == full[:12]
    assert short_construction_hash({"k": "v"}, length=8) == full[:8]


def test_scenario_construction_hash_bytes_are_unchanged_by_the_consolidation() -> None:
    # The exact canonical JSON the pre-M44 inline copy serialized for the families grid
    # (GRID_CONSTRUCTION_VERSION "grid-1.0.0", crash rule tag, roll_down_days [1, 7]),
    # written as a literal — the persisted effective_scenario_version must not move.
    literal = (
        b'{"crash_rule":"crash=min_spot+max_vol",'
        b'"roll_down_days":[1,7],'
        b'"version":"grid-1.0.0"}'
    )
    expected = hashlib.sha256(literal).hexdigest()[:12]
    assert _grid_construction_hash((1, 7)) == expected


def test_surface_construction_hash_bytes_are_unchanged_by_the_consolidation() -> None:
    # Same pin for the cartesian surface (SURFACE_CONSTRUCTION_VERSION "surface-1.0.0"):
    # the canonical JSON of a representative StressSurfaceConfig, written as a literal.
    from algotrading.core.config import StressSurfaceConfig

    surface = StressSurfaceConfig(
        version="stress-test-1",
        spot_shock_abs=0.5,
        vol_shock_abs=0.5,
        spot_steps=5,
        vol_steps=5,
    )
    literal = (
        b'{"spot_shock_abs":0.5,"spot_steps":5,"stress_version":"stress-test-1",'
        b'"version":"surface-1.0.0","vol_shock_abs":0.5,"vol_steps":5}'
    )
    expected = hashlib.sha256(literal).hexdigest()[:12]
    assert _surface_construction_hash(surface) == expected
