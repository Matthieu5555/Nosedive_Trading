"""Shared pytest plumbing for the infra test suite.

One regen flag for every golden test (M36): ``--regen-golden`` rewrites the committed
artifacts under ``tests/golden/`` instead of comparing against them, so blessing the
goldens after an intentional change is one spelling::

    uv run pytest packages/infra/tests -k golden --regen-golden

Regeneration stays a deliberate, reviewable act — the rewritten JSON then shows up in
``git diff``. Each golden test keeps its own bespoke comparison assertions; only the
regenerate-or-load block is shared here.
"""

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
    """Regenerate-or-load for one committed golden artifact.

    Called with the artifact path and the freshly computed summary. Under
    ``--regen-golden`` it rewrites the artifact (byte-identical format to the old
    per-suite regen blocks: sorted keys, 2-space indent, trailing newline) and skips
    the test; otherwise it asserts the artifact exists and returns it parsed, for the
    calling test's own comparisons.
    """

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
