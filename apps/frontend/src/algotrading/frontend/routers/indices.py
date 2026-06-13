"""Indices router: the enabled index set, straight from the registry (single source).

The operator's index selector is driven by this endpoint, NOT a hard-coded list in the web
app. It returns exactly the indices the platform is currently capturing/projecting — the
``enabled`` set of the registry (ADR 0035) loaded from ``configs/universe.yaml``. Parking an
index (``enabled: false``) drops it from this list with no front-end change, and enabling one
makes it appear; the front can never drift from what the backend actually runs.

A registry that fails to load (or names no enabled index) yields a labeled empty payload
(``indices == []``), never a 500.
"""

from __future__ import annotations

from algotrading.core.config import ConfigError
from algotrading.infra.universe import enabled_indices, load_index_registry
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..deps import CtxDep

router = APIRouter(prefix="/api/indices", tags=["indices"])


@router.get("")
def get_indices(ctx: CtxDep) -> JSONResponse:
    """Return the enabled indices (symbol + display name), in canonical registry order.

    The symbol is the platform-wide vocabulary key (e.g. ``SX5E``); ``name`` is the display
    label (e.g. ``EURO STOXX 50``). The list is the registry's ``enabled`` set — the single
    source of which indices exist — so the front selector cannot list an index the backend is
    not capturing.

    A registry that cannot be loaded (no ``configs/`` bundle on this deployment) yields a
    labeled empty payload (``indices == []``), never a 500 — the selector then renders empty
    and disabled rather than fronting an error tile.
    """
    try:
        registry = load_index_registry(ctx.configs_dir)
    except ConfigError:
        return JSONResponse({"indices": []})
    indices = [
        {"symbol": entry.symbol, "name": entry.name}
        for entry in enabled_indices(registry)
    ]
    return JSONResponse({"indices": indices})
