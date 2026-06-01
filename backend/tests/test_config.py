"""Config hashing: cross-process stability and section independence.

The load-bearing claim is that ``config_hash`` is the same in every process and
under any hash seed — otherwise "reproduce the historical run" is a lie. We prove
it by computing the hash in two separate subprocesses launched with *different*
``PYTHONHASHSEED`` values and asserting they agree with each other and with the
in-process value. That is the independent oracle: agreement across processes we
did not coordinate, not a constant copied from the code.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from config import (
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    UniverseConfig,
    config_hash,
    section_hash,
    section_versions,
)

SRC = Path(__file__).resolve().parents[1] / "src"


def make_config(*, solver_version: str = "s1", iv_tolerance: float = 1e-8) -> PlatformConfig:
    return PlatformConfig(
        universe=UniverseConfig(version="u1", underlyings=("AAPL", "MSFT"), exchange="SMART"),
        qc_threshold=QcThresholdConfig(
            version="q1", max_spread_pct=0.05, max_quote_age_seconds=30.0, min_chain_count=6
        ),
        solver=SolverConfig(version=solver_version, iv_tolerance=iv_tolerance, max_iterations=100),
        scenario=ScenarioConfig(
            version="c1", spot_shocks=(-0.1, 0.0, 0.1), vol_shocks=(-0.05, 0.0, 0.05)
        ),
    )


# Rebuilds the identical config in a fresh interpreter and prints its hash.
_SUBPROCESS_SCRIPT = """
from config import (
    PlatformConfig, UniverseConfig, QcThresholdConfig, SolverConfig, ScenarioConfig, config_hash,
)
cfg = PlatformConfig(
    universe=UniverseConfig(version="u1", underlyings=("AAPL", "MSFT"), exchange="SMART"),
    qc_threshold=QcThresholdConfig(
        version="q1", max_spread_pct=0.05, max_quote_age_seconds=30.0, min_chain_count=6
    ),
    solver=SolverConfig(version="s1", iv_tolerance=1e-8, max_iterations=100),
    scenario=ScenarioConfig(
        version="c1", spot_shocks=(-0.1, 0.0, 0.1), vol_shocks=(-0.05, 0.0, 0.05)
    ),
)
print(config_hash(cfg))
"""


def _hash_in_subprocess(hashseed: str) -> str:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hashseed
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    return result.stdout.strip()


def test_config_hash_is_stable_across_processes_and_hash_seeds() -> None:
    # Oracle: two independent processes under different hash seeds must agree.
    seed_one = _hash_in_subprocess("1")
    seed_two = _hash_in_subprocess("2")
    assert seed_one == seed_two
    assert seed_one == config_hash(make_config())
    # It is a real SHA-256 hex digest.
    assert len(seed_one) == 64
    int(seed_one, 16)


def test_bumping_one_version_isolates_to_that_section() -> None:
    base = make_config()
    bumped = make_config(solver_version="s2")
    # The other three sections' hashes are untouched...
    for name in ("universe", "qc_threshold", "scenario"):
        assert section_hash(base, name) == section_hash(bumped, name)
    # ...only the solver section's hash moves...
    assert section_hash(base, "solver") != section_hash(bumped, "solver")
    # ...and the four version stamps stay independent.
    assert section_versions(bumped) == {
        "universe": "u1",
        "qc_threshold": "q1",
        "solver": "s2",
        "scenario": "c1",
    }


def test_changing_any_economic_field_changes_config_hash() -> None:
    base = make_config()
    assert config_hash(make_config(solver_version="s2")) != config_hash(base)
    assert config_hash(make_config(iv_tolerance=1e-7)) != config_hash(base)


def test_default_config_file_loads_and_hashes() -> None:
    # The committed default config parses into a validated object with a hash.
    from config import load_config

    path = Path(__file__).resolve().parents[2] / "configs" / "default.toml"
    config = load_config(path)
    assert config.universe.underlyings == ("AAPL", "MSFT", "SPY")
    assert len(config_hash(config)) == 64
