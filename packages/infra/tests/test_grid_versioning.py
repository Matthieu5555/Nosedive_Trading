from __future__ import annotations

import hashlib

from algotrading.infra.risk.grid_versioning import (
    dedup_preserving_order,
    short_construction_hash,
)
from algotrading.infra.risk.scenarios import _grid_construction_hash
from algotrading.infra.risk.stress_surface import _surface_construction_hash


def test_dedup_keeps_first_seen_order() -> None:
    assert dedup_preserving_order((0.1, -0.1, 0.1, 0.0, -0.1)) == (0.1, -0.1, 0.0)


def test_dedup_is_identity_on_distinct_values() -> None:
    assert dedup_preserving_order((-0.2, 0.0, 0.2)) == (-0.2, 0.0, 0.2)


def test_dedup_of_empty_is_empty() -> None:
    assert dedup_preserving_order(()) == ()


def test_dedup_collapses_an_all_equal_axis_to_one_point() -> None:
    assert dedup_preserving_order((0.0, 0.0, 0.0)) == (0.0,)


def test_short_hash_matches_hand_built_canonical_json() -> None:
    expected = hashlib.sha256(b'{"a":1,"b":2}').hexdigest()[:12]
    assert short_construction_hash({"b": 2, "a": 1}) == expected


def test_short_hash_is_12_hex_chars_by_default_and_length_parametrizable() -> None:
    full = hashlib.sha256(b'{"k":"v"}').hexdigest()
    assert short_construction_hash({"k": "v"}) == full[:12]
    assert short_construction_hash({"k": "v"}, length=8) == full[:8]


def test_scenario_construction_hash_bytes_are_unchanged_by_the_consolidation() -> None:
    literal = (
        b'{"crash_rule":"crash=min_spot+max_vol",'
        b'"roll_down_days":[1,7],'
        b'"version":"grid-1.0.0"}'
    )
    expected = hashlib.sha256(literal).hexdigest()[:12]
    assert _grid_construction_hash((1, 7)) == expected


def test_surface_construction_hash_bytes_are_unchanged_by_the_consolidation() -> None:
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
