from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from ..hashing import sha256_hex
from ..log import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class LoadedConfig:

    data: Mapping[str, Any] = field(compare=False)
    config_hash: str
    sources: tuple[Path, ...] = field(compare=False)


def _stringify_keys(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _stringify_keys(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_stringify_keys(v) for v in value]
    if isinstance(value, float):
        return 0.0 if value == 0.0 else value
    return value


def mapping_config_hash(data: Mapping[str, Any]) -> str:
    normalized = _stringify_keys(data)
    canonical = json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
    )
    return sha256_hex(canonical)


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        _log.error("config file not found: %s", path)
        raise
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError:
        _log.error("invalid YAML in %s", path)
        raise
    loaded = loaded if loaded is not None else {}
    if not isinstance(loaded, Mapping):
        raise ValueError(f"config root must be a mapping, got {type(loaded).__name__}: {path}")
    return dict(loaded)


def load_yaml_config(path: str | Path, base: str | Path | None = None) -> LoadedConfig:
    path = Path(path)
    sources: tuple[Path, ...] = ()
    data: dict[str, Any] = {}
    if base is not None:
        base = Path(base)
        data = _read_yaml(base)
        sources += (base,)
    overlay = _read_yaml(path)
    data = _deep_merge(data, overlay)
    sources += (path,)
    digest = mapping_config_hash(data)
    return LoadedConfig(data=_freeze(data), config_hash=digest, sources=sources)
