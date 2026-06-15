"""Config router: list and read the platform config files (read-only).

Lists the config files under ``ctx.configs_dir`` and serves one file's raw text. The
filename is validated to a bare name so a request can never traverse out of the configs
directory. A missing or non-config file returns a typed ``error`` payload, not a 500.

Also serves the platform-wide **delta-band axis** (``/api/config/delta-bands``) — the single
source of the WS-1F band labels the basket leg selector offers, so the front never hard-codes
a band list (the same no-hard-coded-config-lists rule the registry-driven index selector
follows).
"""

from __future__ import annotations

from pathlib import Path

from algotrading.core.config import ConfigError, load_platform_config
from algotrading.infra.surfaces import ProjectionConfig
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep

router = APIRouter(prefix="/api/config", tags=["config"])

# The config formats we surface. Kept here (top of file) so adding a format is one edit.
_CONFIG_SUFFIXES = (".toml", ".yaml", ".yml")


def _is_config_file(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in _CONFIG_SUFFIXES)


@router.get("")
def list_config_files(ctx: CtxDep) -> JSONResponse:
    """List the available config files (names only)."""
    configs_dir = ctx.configs_dir
    if not configs_dir.exists():
        return JSONResponse({"files": []})
    names = sorted(
        path.name for path in configs_dir.iterdir() if path.is_file() and _is_config_file(path.name)
    )
    return JSONResponse({"files": names})


# Declared before the ``/{filename}`` catch-all so the literal path wins the match (FastAPI
# resolves routes in registration order).
@router.get("/delta-bands")
def get_delta_bands(ctx: CtxDep) -> JSONResponse:
    """Return the ordered delta-band axis (put → ATM → call) the leg selector offers.

    The single source of the band labels: the projection axis built by
    :meth:`ProjectionConfig.from_band` from the **one** band definition in
    ``qc_threshold.grid`` (``band_low_delta``/``band_high_delta``/``band_step``, ADR 0028) — the
    same numbers the projection emits and the grid QC validates, so the selector can never drift
    from the grid the platform actually produces. For the pinned ±30Δ *pas-2* grid that is the
    32 labels ``30dp … 02dp, atm, atmp, 02dc … 30dc``.

    A configs bundle that cannot be loaded (a deployment with no ``configs/``) yields the
    in-memory default axis rather than a 500, so the selector is always populated.
    """
    try:
        grid = load_platform_config(ctx.configs_dir).qc_threshold.grid
    except (ConfigError, OSError):
        bands = ProjectionConfig(version="bff-default").band_labels
    else:
        bands = ProjectionConfig.from_band(
            version="bff-delta-bands",
            band_low_delta=grid.band_low_delta,
            band_high_delta=grid.band_high_delta,
            band_step=grid.band_step,
        ).band_labels
    return JSONResponse({"delta_bands": list(bands)})


@router.get("/{filename}")
def read_config_file(ctx: CtxDep, filename: str) -> JSONResponse:
    """Return one config file's raw text, or a typed error payload."""
    # Reduce to a bare name: no directory traversal can escape the configs dir.
    safe_name = Path(filename).name
    if not _is_config_file(safe_name):
        return JSONResponse(
            {"error": "unsupported_config", "filename": safe_name}, status_code=400
        )
    path = ctx.configs_dir / safe_name
    if not path.is_file():
        return JSONResponse({"error": "not_found", "filename": safe_name}, status_code=404)
    return JSONResponse({"filename": safe_name, "content": path.read_text(encoding="utf-8")})
