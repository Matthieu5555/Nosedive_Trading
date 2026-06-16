from __future__ import annotations

from pathlib import Path

from algotrading.core.config import ConfigError, load_platform_config
from algotrading.infra.surfaces import ProjectionConfig
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep

router = APIRouter(prefix="/api/config", tags=["config"])

_CONFIG_SUFFIXES = (".toml", ".yaml", ".yml")


def _is_config_file(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in _CONFIG_SUFFIXES)


@router.get("")
def list_config_files(ctx: CtxDep) -> JSONResponse:
    configs_dir = ctx.configs_dir
    if not configs_dir.exists():
        return JSONResponse({"files": []})
    names = sorted(
        path.name for path in configs_dir.iterdir() if path.is_file() and _is_config_file(path.name)
    )
    return JSONResponse({"files": names})


@router.get("/delta-bands")
def get_delta_bands(ctx: CtxDep) -> JSONResponse:
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
    safe_name = Path(filename).name
    if not _is_config_file(safe_name):
        return JSONResponse(
            {"error": "unsupported_config", "filename": safe_name}, status_code=400
        )
    path = ctx.configs_dir / safe_name
    if not path.is_file():
        return JSONResponse({"error": "not_found", "filename": safe_name}, status_code=404)
    return JSONResponse({"filename": safe_name, "content": path.read_text(encoding="utf-8")})
