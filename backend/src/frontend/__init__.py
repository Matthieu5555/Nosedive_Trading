"""FastAPI backend-for-frontend (BFF) over the flat backend seams.

This is Workstream M8's frontend spine, built in the current flat layout (per the
taskboard; M0 relocates it to ``apps/frontend`` later). It is the only place where
infra meets HTTP: the routers read our typed contracts back through
:class:`storage.ParquetStore`, fit/aggregate through the pure-function ``surfaces`` and
``risk`` engines, and report health through ``orchestration.build_dashboard`` — then
serialize the result to JSON-primitive payloads. No business logic lives in the
routers; they call infra and serialize, and surface errors as typed payloads rather
than 500s.

The web app (``backend/web``) is the only consumer above this layer.
"""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
