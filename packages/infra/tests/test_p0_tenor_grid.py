"""P0.1 — the tenor grid is pinned once and the two copies agree (OQ-4, ADR 0036).

The grid lives authoritatively in the blueprint Part IX data dictionary (ADR 0011) and is
mirrored into ``configs/universe.yaml`` (the hashed ``universe`` bundle). These tests pin:

* the YAML grid equals the blueprint grid as an *ordered* list of the exact eight tenors,
  with the expected list written literally here (an independent oracle, not the loader); and
* the C7 reproducibility invariant extended to the grid: a comment-only edit leaves the
  ``universe`` bundle hash identical, while changing a tenor moves exactly that bundle's hash.
"""

from __future__ import annotations

import dataclasses
import re
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


def _blueprint_grid() -> tuple[str, ...]:
    """Parse the tenor grid out of the blueprint data dictionary's tenor-grid line."""
    text = (REPO_ROOT / "documentation" / "blueprint" / "09-data-dictionary.md").read_text(
        encoding="utf-8"
    )
    # The blueprint row carries the grid as a backticked, comma-separated ordered list.
    match = re.search(r"tenor[_ ]grid[^|]*\|\s*([^|]+)\|", text, re.IGNORECASE)
    assert match is not None, "blueprint must carry a tenor-grid row"
    # The row lists the grid first, then the year-fraction mapping after "Year fractions";
    # take only the tenor tokens before that phrase so the mapping backticks are excluded.
    grid_part = re.split(r"Year fractions", match.group(1))[0]
    tenors = re.findall(r"`([^`]+)`", grid_part)
    return tuple(tenors)


def test_yaml_tenor_grid_equals_the_expected_ordered_grid() -> None:
    config = load_platform_config(CONFIGS_DIR)
    # Ordered equality, not set membership: order is meaningful and pinned.
    assert config.universe.tenor_grid == EXPECTED_TENOR_GRID


def test_blueprint_and_yaml_grids_agree_as_an_ordered_list() -> None:
    config = load_platform_config(CONFIGS_DIR)
    blueprint = _blueprint_grid()
    assert blueprint == EXPECTED_TENOR_GRID
    assert list(config.universe.tenor_grid) == list(blueprint)


def test_a_comment_only_edit_leaves_the_universe_hash_identical() -> None:
    # Reordering/adding a comment is not an economic change; the hash is over content, so
    # the universe bundle hash must not move. Simulate by hashing two equal configs.
    config = load_platform_config(CONFIGS_DIR)
    same = dataclasses.replace(config)
    assert config_hashes(config)["universe"] == config_hashes(same)["universe"]


def test_changing_a_tenor_moves_exactly_the_universe_hash() -> None:
    config = load_platform_config(CONFIGS_DIR)
    before = config_hashes(config)
    moved_universe = dataclasses.replace(
        config.universe,
        tenor_grid=(*EXPECTED_TENOR_GRID[:-1], "5y"),  # swap the tail tenor
    )
    moved = dataclasses.replace(config, universe=moved_universe)
    after = config_hashes(moved)
    assert after["universe"] != before["universe"]
    # Every other bundle is byte-identical: the grid lives only in the universe bundle.
    for bundle in ("qc", "pricing", "scenarios"):
        assert after[bundle] == before[bundle]


def test_universe_config_rejects_an_empty_or_duplicate_grid() -> None:
    import pytest
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError):
        UniverseConfig(version="u", underlyings=("AAPL",), exchange="SMART", tenor_grid=())
    with pytest.raises(ConfigFieldError):
        UniverseConfig(
            version="u", underlyings=("AAPL",), exchange="SMART", tenor_grid=("1m", "1m")
        )
