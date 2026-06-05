"""Prove the layering guard *bites* — don't just trust the import-linter config.

TESTING.md and the M0 spec are explicit: assert that a planted upward import makes
`lint-imports` fail. A green config that never sees a violation is not evidence the
guard works. This plants an `infra -> strategy` import (illegal: infra is blind to
alpha and strategy sits above it), runs the real linter, asserts it reports a broken
contract, and always removes the probe.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PROBE = _REPO_ROOT / "packages/infra/src/algotrading/infra/_guard_probe.py"
_LINT_IMPORTS = Path(sys.executable).parent / "lint-imports"


def _run_linter() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_LINT_IMPORTS)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_clean_tree_passes_the_linter() -> None:
    result = _run_linter()
    assert result.returncode == 0, f"clean tree should pass:\n{result.stdout}\n{result.stderr}"


def test_planted_upward_import_breaks_a_contract() -> None:
    _PROBE.write_text(
        "# Temporary probe planted by the M0 layering-guard test. Removed in finally.\n"
        "import algotrading.strategy  # infra -> strategy is illegal (blind to alpha).\n",
        encoding="utf-8",
    )
    try:
        result = _run_linter()
    finally:
        _PROBE.unlink(missing_ok=True)

    assert result.returncode != 0, (
        "the linter must FAIL on an infra->strategy import, but it passed:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    assert "broken" in result.stdout.lower(), (
        f"expected a broken contract to be reported:\n{result.stdout}"
    )
