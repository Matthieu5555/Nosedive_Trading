from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

__all__ = ["DATA_ROOT_ENV_VAR", "data_root", "load_env_file", "repo_root"]

DATA_ROOT_ENV_VAR = "ALGOTRADING_DATA_ROOT"

_REPO_ROOT = Path(__file__).resolve().parents[5]


def repo_root() -> Path:
    return _REPO_ROOT


def data_root() -> Path:
    override = os.environ.get(DATA_ROOT_ENV_VAR)
    return Path(override) if override else _REPO_ROOT / "data"


def load_env_file(path: str | Path | None = None) -> bool:
    file = Path(path) if path is not None else _REPO_ROOT / ".env"
    return load_dotenv(file, override=False, interpolate=False)
