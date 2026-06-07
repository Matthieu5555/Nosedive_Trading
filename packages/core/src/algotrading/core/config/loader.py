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

import dataclasses
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .platform_config import (
    ForwardConfig,
    GridQcConfig,
    MonetizationConfig,
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    StressSurfaceConfig,
    StrikeSelectionConfig,
    SurfaceConfig,
    UniverseConfig,
)
from .reflective import ConfigFieldError, build_dataclass
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
    "scenario": (ScenarioConfig, "scenarios.yaml", "scenario"),
    "monetization": (MonetizationConfig, "scenarios.yaml", "monetization"),
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
        section_data = data[name]
        if name == "universe":
            built[name] = _build_universe(section_data)
        elif name == "qc_threshold":
            built[name] = _build_qc_threshold(section_data)
        elif name == "scenario":
            built[name] = _build_scenario(section_data)
        else:
            built[name] = build_dataclass(cls, section_data, section=name)
    return PlatformConfig(**built)


def _build_scenario(section_data: Mapping[str, Any]) -> ScenarioConfig:
    """Build :class:`ScenarioConfig`, handling the nested ``stress_surface:`` block (WS 2B).

    The flat scalar/tuple shock fields are coerced by the reflective :func:`build_dataclass`
    seam. The ``stress_surface:`` block (the 2B ±range cartesian surface grid) is itself a
    flat scalar dataclass, so it is built through the *same* seam and reattached — it
    canonicalizes into ``config_hashes["scenarios"]`` like any other field. The block is
    **required** on the load path (no silent default for the economic stress grid): an absent
    ``stress_surface:`` raises rather than falling back to the dataclass placeholder default
    that older in-memory constructions use, the same discipline ``qc_threshold.grid`` follows.
    """
    if not isinstance(section_data, Mapping):
        raise ConfigError("config section 'scenario' must be a mapping")
    scalar_fields = {k: v for k, v in section_data.items() if k != "stress_surface"}
    base = build_dataclass(
        ScenarioConfig,
        scalar_fields,
        section="scenario",
        caller_supplied=frozenset({"stress_surface"}),
    )
    if "stress_surface" not in section_data:
        raise ConfigError("config section 'scenario' is missing the 'stress_surface:' block")
    ss_data = section_data["stress_surface"]
    if not isinstance(ss_data, Mapping):
        raise ConfigError("config 'scenario.stress_surface' must be a mapping")
    surface = build_dataclass(StressSurfaceConfig, ss_data, section="stress_surface")
    return dataclasses.replace(base, stress_surface=surface)


def _build_qc_threshold(section_data: Mapping[str, Any]) -> QcThresholdConfig:
    """Build :class:`QcThresholdConfig`, handling the nested ``grid:`` block specially (WS 1H).

    The flat scalar cut-offs are coerced by the reflective :func:`build_dataclass` seam. The
    ``grid:`` block (the grid-aware QC cut-offs) carries ``tenor_floors``, a ``tenor → int``
    mapping the flat coercion cannot type, so it is split out: the scalar grid fields go
    through :func:`build_dataclass`, the ``tenor_floors`` map is coerced by hand to a plain
    ``{str: int}`` dict (rejecting a non-mapping or a non-integer floor with a labelled
    :class:`ConfigFieldError`), and the typed :class:`GridQcConfig` is reattached. The block
    is required on the load path (no silent default for the economic floors); an absent
    ``grid:`` raises rather than falling back to the dataclass default the way older in-memory
    constructions may.
    """
    if not isinstance(section_data, Mapping):
        raise ConfigError("config section 'qc_threshold' must be a mapping")
    scalar_fields = {k: v for k, v in section_data.items() if k != "grid"}
    base = build_dataclass(
        QcThresholdConfig,
        scalar_fields,
        section="qc_threshold",
        caller_supplied=frozenset({"grid"}),
    )
    if "grid" not in section_data:
        raise ConfigError("config section 'qc_threshold' is missing the 'grid:' block")
    grid_data = section_data["grid"]
    if not isinstance(grid_data, Mapping):
        raise ConfigError("config 'qc_threshold.grid' must be a mapping")
    grid_scalars = {k: v for k, v in grid_data.items() if k != "tenor_floors"}
    grid_base = build_dataclass(
        GridQcConfig,
        grid_scalars,
        section="grid_qc",
        caller_supplied=frozenset({"tenor_floors"}),
    )
    if "tenor_floors" not in grid_data:
        raise ConfigError("config 'qc_threshold.grid' is missing 'tenor_floors'")
    raw_floors = grid_data["tenor_floors"]
    if not isinstance(raw_floors, Mapping):
        raise ConfigError(
            "config 'qc_threshold.grid.tenor_floors' must be a mapping of tenor → floor"
        )
    floors = {
        str(tenor): _coerce_floor(tenor, value) for tenor, value in raw_floors.items()
    }
    grid = dataclasses.replace(grid_base, tenor_floors=floors)
    return dataclasses.replace(base, grid=grid)


def _coerce_floor(tenor: Any, value: Any) -> int:
    """Coerce one tenor-floor entry to an int, rejecting a bool or non-integral value."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigFieldError(
            "grid_qc", "tenor_floors", {tenor: value}, "floor must be an integer"
        )
    return value


def _build_universe(section_data: Mapping[str, Any]) -> UniverseConfig:
    """Build :class:`UniverseConfig`, handling the nested ``indices:`` and ``strike_selection:``
    blocks specially.

    The reflective :func:`build_dataclass` coerces flat scalar/tuple fields, but two nested
    blocks need special handling. The ``indices:`` block (ADR 0035) is a keyed map of nested
    maps it cannot coerce, so it is split out and reattached canonicalized — a stable,
    JSON-ready nested structure so it folds into ``config_hashes["universe"]`` deterministically
    (no separate hash); it is *not* validated here (the calendar-code check is the infra layer's
    ``parse_index_registry``, which core stays blind to). The ``strike_selection:`` block (WS
    1B, ADR 0028) is itself a flat scalar dataclass, so it *is* built reflectively through the
    same :func:`build_dataclass` seam (no silent default for the economic delta bound) and the
    typed object is reattached — it canonicalizes into the universe hash like any other field.
    Both blocks are absent-tolerant: a missing ``indices:`` is an empty registry; a missing
    ``strike_selection:`` falls back to the dataclass default (used by older configs/tests).
    """
    if not isinstance(section_data, Mapping):
        raise ConfigError("config section 'universe' must be a mapping")
    nested = frozenset({"indices", "strike_selection"})
    scalar_fields = {k: v for k, v in section_data.items() if k not in nested}
    base = build_dataclass(
        UniverseConfig, scalar_fields, section="universe", caller_supplied=nested
    )
    raw_indices = section_data.get("indices", {})
    if not isinstance(raw_indices, Mapping):
        raise ConfigError("config 'universe.indices' must be a mapping of index symbol → entry")
    replacements: dict[str, Any] = {"indices": _canonical_indices(raw_indices)}
    if "strike_selection" in section_data:
        ss_data = section_data["strike_selection"]
        if not isinstance(ss_data, Mapping):
            raise ConfigError("config 'universe.strike_selection' must be a mapping")
        replacements["strike_selection"] = build_dataclass(
            StrikeSelectionConfig, ss_data, section="strike_selection"
        )
    return dataclasses.replace(base, **replacements)


def _canonical_indices(value: Any) -> Any:
    """Return a deeply-immutable, JSON-ready copy of the indices block for stable hashing."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): _canonical_indices(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_canonical_indices(v) for v in value)
    return value


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
