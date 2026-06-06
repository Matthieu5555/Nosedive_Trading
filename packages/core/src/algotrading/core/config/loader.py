"""Build a :class:`PlatformConfig` from a versioned YAML overlay config or a mapping.

The loader is the only place that knows the on-disk shape of the typed economic
config. It turns lists into the tuples the frozen dataclasses expect and raises a
clear error naming the missing section rather than a raw ``KeyError`` from deep inside.

The economic config is authored in versioned YAML and resolved through the overlay
loader (``from_config`` over a :class:`LoadedConfig` from ``load_yaml_config`` — base +
one overlay, deep-merged), then validated by ``config_from_mapping``. This is the single
path C7/ADR 0028 standardize on; the legacy TOML loader was retired here.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .platform_config import (
    ForwardConfig,
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    SurfaceConfig,
    UniverseConfig,
)
from .reflective import build_dataclass
from .yaml_config import LoadedConfig, load_yaml_config

# Each PlatformConfig section, mapped to the typed class that builds it and the bundle
# file + in-file key it is authored under (blueprint Part VII taxonomy). A ``subkey`` of
# ``None`` means the whole file root is the section; a string means the section is that
# nested block (``pricing.yaml`` carries ``solver:`` and ``surface:`` side by side). The
# two operational bundles — ``environment.yaml`` (paths, log levels) and ``broker.yaml``
# (client-id bands, reconnect policy) — travel a separate, un-hashed path and are
# deliberately absent here: nothing in them changes which records exist or their values.
_PLATFORM_SECTIONS: dict[str, tuple[type, str, str | None]] = {
    "universe": (UniverseConfig, "universe.yaml", None),
    "qc_threshold": (QcThresholdConfig, "qc.yaml", None),
    "solver": (SolverConfig, "pricing.yaml", "solver"),
    "surface": (SurfaceConfig, "pricing.yaml", "surface"),
    "forward": (ForwardConfig, "pricing.yaml", "forward"),
    "scenario": (ScenarioConfig, "scenarios.yaml", None),
}


class ConfigError(Exception):
    """The config file or mapping was missing a required section or field."""


def config_from_mapping(data: Mapping[str, Any]) -> PlatformConfig:
    """Build a validated config from a plain mapping (e.g. resolved YAML).

    Each economic section is built by the one reflective :func:`build_dataclass` seam
    (coerce by declared type, reject unknown/missing keys, validate in ``__post_init__``),
    so the YAML↔dataclass schema cannot drift and a bad field raises a labelled
    :class:`ConfigFieldError` naming the section and field. ``data`` is keyed by section
    name (``universe``, ``qc_threshold``, ``solver``, ``surface``, ``scenario``).
    """
    built: dict[str, Any] = {}
    for name, (cls, _filename, _subkey) in _PLATFORM_SECTIONS.items():
        if name not in data:
            raise ConfigError(f"config is missing required section '{name}'")
        built[name] = build_dataclass(cls, data[name], section=name)
    return PlatformConfig(**built)


def from_config(loaded: LoadedConfig) -> PlatformConfig:
    """Build a validated :class:`PlatformConfig` from a resolved YAML overlay config.

    The economic config is authored in versioned YAML and resolved through the overlay
    loader (``load_yaml_config`` — base + one overlay, deep-merged), then validated into
    the frozen dataclasses by the *same* ``config_from_mapping`` the TOML path uses. This
    is the unified typed entry C7/ADR 0028 standardize on: one schema, one validation, the
    overlay loader's inheritance instead of a second untyped path.

    The four required sections (``universe``, ``qc_threshold``, ``solver``, ``scenario``)
    must be present in the resolved mapping; a missing one raises :class:`ConfigError`.
    """
    return config_from_mapping(dict(loaded.data))


def load_platform_config(configs_dir: str | Path) -> PlatformConfig:
    """Build the validated :class:`PlatformConfig` from the Part VII bundle files.

    Reads the economic bundles in ``configs_dir`` (``universe.yaml``, ``qc.yaml``,
    ``pricing.yaml``, ``scenarios.yaml``) — each authored per the blueprint Part VII
    taxonomy — and assembles them into the typed config through the one reflective
    :func:`config_from_mapping` seam. A bundle may carry more than one section:
    ``pricing.yaml`` holds both ``solver:`` and ``surface:``. The operational bundles
    (``environment.yaml``, ``broker.yaml``) are not loaded here: they are not economics
    and must not enter the reproducibility hashes.

    A missing bundle file raises :class:`ConfigError` naming the file, and a missing
    in-file section block raises one naming the block — rather than a bare
    ``FileNotFoundError``/``KeyError`` from deep inside — so a misconfigured deployment
    fails loudly.
    """
    configs_dir = Path(configs_dir)
    files: dict[str, Mapping[str, Any]] = {}
    mapping: dict[str, Any] = {}
    for section, (_cls, filename, subkey) in _PLATFORM_SECTIONS.items():
        if filename not in files:
            try:
                files[filename] = load_yaml_config(configs_dir / filename).data
            except FileNotFoundError as exc:
                raise ConfigError(f"config bundle '{filename}' not found in {configs_dir}") from exc
        contents = files[filename]
        if subkey is None:
            mapping[section] = dict(contents)
        else:
            if subkey not in contents:
                raise ConfigError(f"config bundle '{filename}' is missing the '{subkey}:' block")
            mapping[section] = dict(contents[subkey])
    return config_from_mapping(mapping)
