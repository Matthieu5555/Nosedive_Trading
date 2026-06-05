"""Config router: list and read the platform config files (read-only).

Lists the config files under ``ctx.configs_dir`` and serves one file's raw text. The
filename is validated to a bare name so a request can never traverse out of the configs
directory. A missing or non-config file returns a typed ``error`` payload, not a 500.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..context import AppContext

router = APIRouter(prefix="/api/config", tags=["config"])

# The config formats we surface. Kept here (top of file) so adding a format is one edit.
_CONFIG_SUFFIXES = (".toml", ".yaml", ".yml")


def _context(request: Request) -> AppContext:
    return request.app.state.ctx


def _is_config_file(name: str) -> bool:
    return any(name.endswith(suffix) for suffix in _CONFIG_SUFFIXES)


@router.get("")
def list_config_files(request: Request) -> JSONResponse:
    """List the available config files (names only)."""
    ctx = _context(request)
    configs_dir = ctx.configs_dir
    if not configs_dir.exists():
        return JSONResponse({"files": []})
    names = sorted(
        path.name for path in configs_dir.iterdir() if path.is_file() and _is_config_file(path.name)
    )
    return JSONResponse({"files": names})


@router.get("/{filename}")
def read_config_file(request: Request, filename: str) -> JSONResponse:
    """Return one config file's raw text, or a typed error payload."""
    ctx = _context(request)
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
