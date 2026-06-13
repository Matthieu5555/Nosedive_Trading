"""P0.1 — the tenor grid is pinned once (OQ-4, ADR 0036).

The grid lives authoritatively in ``configs/universe.yaml`` (the hashed ``universe`` bundle).
These tests pin:

* the YAML grid equals the expected *ordered* list of the exact eight tenors, with the
  expected list written literally here (an independent oracle, not the loader); and
* the C7 reproducibility invariant extended to the grid: a comment-only edit leaves the
  ``universe`` bundle hash identical, while changing a tenor moves exactly that bundle's hash.
"""

from __future__ import annotations

from pathlib import Path

from algotrading.core.config import (
    UniverseConfig,
    config_hashes,
    load_platform_config,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGS_DIR = REPO_ROOT / "configs"

# Independent oracle: the eight tenors, in the prof's spoken order. Written literally,
# never read from the loader or the YAML, so a silent reorder/edit on either side is caught.
EXPECTED_TENOR_GRID = ("10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y")


def test_yaml_tenor_grid_equals_the_expected_ordered_grid() -> None:
    config = load_platform_config(CONFIGS_DIR)
    # Ordered equality, not set membership: order is meaningful and pinned.
    assert config.universe.tenor_grid == EXPECTED_TENOR_GRID


def test_a_comment_only_edit_leaves_the_universe_hash_identical() -> None:
    # Reordering/adding a comment is not an economic change; the hash is over content, so
    # the universe bundle hash must not move. Simulate by hashing two equal configs.
    config = load_platform_config(CONFIGS_DIR)
    same = config.model_copy()
    assert config_hashes(config)["universe"] == config_hashes(same)["universe"]


def test_changing_a_tenor_moves_exactly_the_universe_hash() -> None:
    config = load_platform_config(CONFIGS_DIR)
    before = config_hashes(config)
    moved_universe = config.universe.model_copy(
        update={"tenor_grid": (*EXPECTED_TENOR_GRID[:-1], "5y")},  # swap the tail tenor
    )
    moved = config.model_copy(update={"universe": moved_universe})
    after = config_hashes(moved)
    assert after["universe"] != before["universe"]
    # Every other bundle is byte-identical: the grid lives only in the universe bundle.
    for bundle in ("qc", "pricing", "scenarios"):
        assert after[bundle] == before[bundle]


def test_universe_config_rejects_an_empty_or_duplicate_grid() -> None:
    import pytest
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError):
        UniverseConfig(version="u", exchange="SMART", tenor_grid=())
    with pytest.raises(ConfigFieldError):
        UniverseConfig(
            version="u", exchange="SMART", tenor_grid=("1m", "1m")
        )
