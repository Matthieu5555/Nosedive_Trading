from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

GoldenArtifact = Callable[[Path, dict[str, Any]], dict[str, Any]]


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--regen-golden",
        action="store_true",
        default=False,
        help="rewrite the committed golden artifacts instead of comparing against them",
    )


@pytest.fixture()
def golden_artifact(request: pytest.FixtureRequest) -> GoldenArtifact:

    def regenerate_or_load(path: Path, summary: dict[str, Any]) -> dict[str, Any]:
        if request.config.getoption("--regen-golden"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
            pytest.skip(f"regenerated golden artifact at {path}")
        assert path.exists(), (
            f"missing golden artifact {path}; regenerate with "
            f"uv run pytest {Path(str(request.node.path)).name} -k golden --regen-golden"
        )
        return json.loads(path.read_text())

    return regenerate_or_load
