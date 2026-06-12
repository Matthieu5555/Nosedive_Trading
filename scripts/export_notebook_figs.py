"""Re-render the vol-surface notebook figures to PNG (documentation/vol-surface/assets).

Runs the notebook's code cells with ``Figure.show`` monkeypatched to ``write_image``,
so each figure is exported at its own design height and a width scaled by column count
(single plots stay wide-and-short, multi-panel subplots get room to breathe). Requires
``kaleido`` (in the ``notebooks`` dependency group). Re-run after editing the notebook,
then rebuild the PDF with ``scripts/export_doc_pdf.py``.

Usage:
    uv run --group notebooks python scripts/export_notebook_figs.py
"""

import nbformat
import plotly.graph_objects as go
from algotrading.core.paths import repo_root

NOTEBOOK = repo_root() / "notebooks" / "vol_surface_pedagogique.ipynb"
OUT = repo_root() / "documentation" / "vol-surface" / "assets"

# Figures in order of appearance (one Figure.show() per plotting cell).
SLUGS = [
    "1-1_payoff", "1-2_dispersion", "1-3_prix_vs_vol", "1-4_inversion",
    "1-5_pourquoi_pas_plate", "2-1_delta", "2-2_vega", "2-3_gamma",
    "2-4_theta", "2-5_rho", "2-6_dashboard", "3-1_quatre_smiles",
    "3-2_trois_parametres", "4-1_tranche_surface", "4-2_term_structure",
    "4-3_surface_actions", "4-4_surface_crypto", "4-5_violations", "4-6_coupes",
]
_seen = {"n": 0}


def _save(self: go.Figure, *args: object, **kwargs: object) -> None:
    i = _seen["n"]
    slug = SLUGS[i] if i < len(SLUGS) else f"extra_{i}"
    grid = getattr(self, "_grid_ref", None)
    ncols = len(grid[0]) if grid else 1
    width = {1: 1000, 2: 1150}.get(ncols, 1500)
    height = int(self.layout.height or 420)
    self.write_image(str(OUT / f"{slug}.png"), width=width, height=height, scale=2)
    _seen["n"] = i + 1


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    go.Figure.show = _save
    nb = nbformat.read(NOTEBOOK, as_version=4)
    ns: dict = {}
    for idx, cell in enumerate(nb.cells):
        if cell.cell_type == "code":
            exec(compile(cell.source, f"<cell {idx}>", "exec"), ns)  # noqa: S102
    print(f"exported {_seen['n']} figures to {OUT}")


if __name__ == "__main__":
    main()
