from __future__ import annotations

from pathlib import Path

from algotrading.core.config import (
    UniverseConfig,
    config_hashes,
    load_platform_config,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIGS_DIR = REPO_ROOT / "configs"

EXPECTED_TENOR_GRID = ("10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y")


def test_yaml_tenor_grid_equals_the_expected_ordered_grid() -> None:
    config = load_platform_config(CONFIGS_DIR)
    assert config.universe.tenor_grid == EXPECTED_TENOR_GRID


def test_a_comment_only_edit_leaves_the_universe_hash_identical() -> None:
    config = load_platform_config(CONFIGS_DIR)
    same = config.model_copy()
    assert config_hashes(config)["universe"] == config_hashes(same)["universe"]


def test_changing_a_tenor_moves_exactly_the_universe_hash() -> None:
    config = load_platform_config(CONFIGS_DIR)
    before = config_hashes(config)
    moved_universe = config.universe.model_copy(
        update={"tenor_grid": (*EXPECTED_TENOR_GRID[:-1], "5y")},
    )
    moved = config.model_copy(update={"universe": moved_universe})
    after = config_hashes(moved)
    assert after["universe"] != before["universe"]
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
