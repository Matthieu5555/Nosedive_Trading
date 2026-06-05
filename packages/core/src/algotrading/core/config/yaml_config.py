"""Versioned YAML configuration loading with a deterministic content hash.

The blueprint keeps economic inputs in versioned config files (Part VII), not as
constants in code. This is the generic, untyped loader for those YAML artifacts: a
base config can be specialized by an overlay (inheritance), and the resolved config
carries a ``mapping_config_hash`` so any output can record which inputs produced it.

It complements the typed :class:`~algotrading.core.config.PlatformConfig` path: use
the typed loader when the four economic sections are required with field validation,
and this overlay loader for free-form versioned YAML bundles (calendars, exchange
tables, per-broker settings) where inheritance is the point.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from ..log import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True)
class LoadedConfig:
    """A resolved configuration with its provenance.

    ``data`` is deeply read-only. Equality and hashing are defined on ``config_hash``
    alone, so a config is safe to use as a cache key by its content.
    """

    data: Mapping[str, Any] = field(compare=False)
    config_hash: str
    sources: tuple[Path, ...] = field(compare=False)


def _stringify_keys(value: Any) -> Any:
    """Return ``value`` with every mapping key coerced to ``str``.

    YAML allows non-string keys (ints, bools); coercing them keeps the canonical
    serialization sortable and stable.
    """
    if isinstance(value, Mapping):
        return {str(k): _stringify_keys(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_stringify_keys(v) for v in value]
    return value


def mapping_config_hash(data: Mapping[str, Any]) -> str:
    """Return a deterministic SHA-256 over free-form config content.

    Key order does not affect the hash; identical content always hashes identically.
    The typed-config equivalent is ``config.config_hash`` over a ``PlatformConfig``.
    """
    normalized = _stringify_keys(data)
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` onto ``base``; overlay wins on conflict."""
    merged = dict(base)
    for key, value in overlay.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(existing, value)
        else:
            merged[key] = value
    return merged


def _freeze(value: Any) -> Any:
    """Return a deeply immutable view: mappings to read-only proxies, lists to tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({k: _freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(v) for v in value)
    return value


def _read_yaml(path: Path) -> dict[str, Any]:
    """Load a YAML file into a mapping; log and re-raise on a missing file or invalid YAML."""
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
    """Load a YAML config, optionally overlaying it onto a base config.

    Raises:
        FileNotFoundError: if a referenced file does not exist.
        ValueError: if a YAML root is not a mapping.
        yaml.YAMLError: if a file is not valid YAML.
    """
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
    # Hash the plain data, then store a deeply-immutable view so the hash cannot drift.
    digest = mapping_config_hash(data)
    return LoadedConfig(data=_freeze(data), config_hash=digest, sources=sources)
