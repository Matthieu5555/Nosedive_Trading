"""Doc-freshness guard — the mechanical backstop for the "keep the docs alive" rule.

AGENTS.md asks every change to update the doc next to the code; that rule was
convention-only and silently re-drifted. This test is the gate-wired guard H2
added so the README ladder, the routing map, and the `documentation/modules/`
mirror cannot rot unnoticed. It is deliberately fast and dependency-free (stdlib
only, no git subprocess) so it rides the root `pytest` gate.

What it asserts:
  * every `packages/*` package has a `README.md`;
  * every module dir under the `algotrading.infra` analytics core has one;
  * every `documentation/modules/` symlink resolves to a real file;
  * `.agent/map.md` routes every canonical top-level area (and no new canonical
    top-level dir appeared without being added to the map);
  * no relative markdown link in the map or the package/module READMEs is dead.

It does NOT check prose quality or that every named symbol exists — that stays a
human review concern (H2 Task 1/2). This guard only keeps the *structure* honest.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    """Walk up from this file to the dir holding both pyproject.toml and .agent."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / ".agent").is_dir():
            return parent
    raise RuntimeError("repo root (pyproject.toml + .agent) not found above this test")


ROOT = _repo_root()
INFRA_CORE = ROOT / "packages" / "infra" / "src" / "algotrading" / "infra"

# Canonical top-level areas the routing map must point at. Adding a new canonical
# top-level dir is meant to fail this test until both this set and .agent/map.md
# are updated together — that is the anti-drift point, not an accident.
REQUIRED_AREAS = {
    "packages",
    "apps",
    "documentation",
    "scripts",
    "configs",
    "notebooks",
    "research",
    "data",
    "tasks",
}

# Non-routable top-level dirs: reference checkouts (kept in place, flagged by their
# own README banners — see the H1 audit) and hidden dot-dirs (rulebook, caches, vcs).
REFERENCE_DIRS = {"Test Lenny", "Vincent's Code", "ThomasHossen"}

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _module_dirs() -> list[Path]:
    """Every infra analytics-core module dir (excludes caches)."""
    return [
        d
        for d in INFRA_CORE.rglob("*")
        if d.is_dir() and d.name != "__pycache__"
    ]


def _docs_with_relative_links() -> list[Path]:
    docs = [ROOT / ".agent" / "map.md", ROOT / "README.md", ROOT / "documentation" / "README.md"]
    docs += sorted((ROOT / "packages").glob("*/README.md"))
    docs += sorted(INFRA_CORE.rglob("README.md"))
    return [d for d in docs if d.is_file()]


def test_every_package_has_a_readme() -> None:
    missing = [
        p.name
        for p in sorted((ROOT / "packages").iterdir())
        if p.is_dir() and not (p / "README.md").is_file()
    ]
    assert not missing, f"packages/ subdirs without README.md: {missing}"


def test_every_infra_module_dir_has_a_readme() -> None:
    missing = [
        str(d.relative_to(ROOT))
        for d in _module_dirs()
        if not (d / "README.md").is_file()
    ]
    assert not missing, f"infra module dirs without README.md: {missing}"


def test_documentation_modules_symlinks_resolve() -> None:
    mirror = ROOT / "documentation" / "modules"
    links = [p for p in mirror.iterdir() if p.is_symlink()]
    assert links, "documentation/modules/ has no symlinks — mirror is empty"
    broken = [p.name for p in links if not p.resolve().is_file()]
    assert not broken, f"broken documentation/modules/ symlinks: {broken}"


def test_map_routes_every_canonical_top_level_area() -> None:
    map_text = (ROOT / ".agent" / "map.md").read_text(encoding="utf-8")
    unrouted = [area for area in sorted(REQUIRED_AREAS) if area not in map_text]
    assert not unrouted, f".agent/map.md does not route these top-level areas: {unrouted}"
    # The rulebook itself must be routable too.
    assert ".agent" in map_text, ".agent/map.md must reference the .agent rulebook"


def test_no_unrouted_canonical_top_level_dir_appeared() -> None:
    """A new canonical top-level dir must be added to the map (and REQUIRED_AREAS)."""
    actual = {
        p.name
        for p in ROOT.iterdir()
        if p.is_dir() and not p.name.startswith(".") and p.name not in REFERENCE_DIRS
    }
    unexpected = actual - REQUIRED_AREAS
    assert not unexpected, (
        f"new canonical top-level dir(s) not in the routing map: {sorted(unexpected)} "
        "— add them to .agent/map.md and to REQUIRED_AREAS, or move them under a "
        "reference/ignored location"
    )


@pytest.mark.parametrize("doc", _docs_with_relative_links(), ids=lambda d: str(d.relative_to(ROOT)))
def test_no_dead_relative_links(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    dead = []
    for match in _LINK_RE.finditer(text):
        target = match.group(1).strip()
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#")[0]
        if not path:
            continue
        # Tolerate both link bases authors use: relative to the doc, or repo-root.
        if not ((doc.parent / path).exists() or (ROOT / path).exists()):
            dead.append(target)
    assert not dead, f"dead relative link(s) in {doc.relative_to(ROOT)}: {dead}"
