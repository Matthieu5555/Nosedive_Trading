from __future__ import annotations

import re
from pathlib import Path

import pytest


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file() and (parent / ".agent").is_dir():
            return parent
    raise RuntimeError("repo root (pyproject.toml + .agent) not found above this test")


ROOT = _repo_root()
INFRA_CORE = ROOT / "packages" / "infra" / "src" / "algotrading" / "infra"

REQUIRED_AREAS = {
    "packages",
    "apps",
    "scripts",
    "configs",
    "notebooks",
    "research",
    "data",
    "tasks",
    "docs",
}

REFERENCE_DIRS = {"ThomasHossen"}

_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _module_dirs() -> list[Path]:
    return [
        d
        for d in INFRA_CORE.rglob("*")
        if d.is_dir() and d.name != "__pycache__"
    ]


def _docs_with_relative_links() -> list[Path]:
    docs = [ROOT / ".agent" / "map.md", ROOT / "README.md"]
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


def test_map_routes_every_canonical_top_level_area() -> None:
    map_text = (ROOT / ".agent" / "map.md").read_text(encoding="utf-8")
    unrouted = [area for area in sorted(REQUIRED_AREAS) if area not in map_text]
    assert not unrouted, f".agent/map.md does not route these top-level areas: {unrouted}"
    assert ".agent" in map_text, ".agent/map.md must reference the .agent rulebook"


def test_no_unrouted_canonical_top_level_dir_appeared() -> None:
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
        if not ((doc.parent / path).exists() or (ROOT / path).exists()):
            dead.append(target)
    assert not dead, f"dead relative link(s) in {doc.relative_to(ROOT)}: {dead}"
