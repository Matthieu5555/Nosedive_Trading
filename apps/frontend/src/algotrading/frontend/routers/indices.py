from __future__ import annotations

from algotrading.core.config import ConfigError, ConfigFieldError
from algotrading.infra.universe import enabled_indices, load_index_registry
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep

router = APIRouter(prefix="/api/indices", tags=["indices"])


@router.get("")
def get_indices(ctx: CtxDep) -> JSONResponse:
    try:
        registry = load_index_registry(ctx.configs_dir)
    except (ConfigError, ConfigFieldError):
        return JSONResponse({"indices": []})
    indices = [
        {"symbol": entry.symbol, "name": entry.name, "currency": entry.currency}
        for entry in enabled_indices(registry)
    ]
    return JSONResponse({"indices": indices})
