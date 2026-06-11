"""Build a :class:`PlatformConfig` from a versioned YAML overlay config or a mapping.

The loader is the only place that knows the on-disk shape of the typed economic
config. It hands the resolved YAML mapping straight to the pydantic v2 section models,
which validate it: nested blocks (``universe.indices``, ``universe.strike_selection``,
``qc_threshold.grid.tenor_floors``, ``scenario.stress_surface``) are native nested
models / ``dict[str, int]`` fields on the section classes, so there is no hand-rolled
coercion or escape-hatch builder — the schema *is* the validation.

The economic config is authored in versioned YAML and resolved through the overlay
loader (``from_config`` over a :class:`LoadedConfig` from ``load_yaml_config`` — base +
one overlay, deep-merged), then validated by ``config_from_mapping``. This is the single
path C7/ADR 0028 standardize on; the legacy TOML loader was retired here.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn

from .platform_config import (
    ConfigFieldError,
    ForwardConfig,
    MonetizationConfig,
    PlatformConfig,
    QcThresholdConfig,
    ScenarioConfig,
    SolverConfig,
    SurfaceConfig,
    UniverseConfig,
)
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


def _plain(value: Any) -> Any:
    """Return a deep copy of ``value`` as plain ``dict``/``list`` containers.

    The overlay loader freezes its resolved data into read-only ``MappingProxyType`` maps
    and ``tuple`` sequences. pydantic's strict mode rejects a ``MappingProxyType`` where a
    nested model (``strike_selection``, ``grid``, ``stress_surface``) is declared, so the
    loader — which owns the on-disk shape — normalises a frozen section back to plain
    ``dict``/``list`` before validation. The section models re-freeze on the way in
    (tuples via ``_list_to_tuple``, the ``indices`` block via its ``model_validator``), so
    nothing leaks a mutable container into the frozen config.
    """
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _relabel(section: str, exc: ConfigFieldError) -> NoReturn:
    """Re-raise ``exc`` under the loader's bundle ``section`` name, preserving the rest.

    The section models' ``_ConfigModel`` base already raises a :class:`ConfigFieldError`,
    but labels it with the *model class name* (``QcThresholdConfig``). The loader knows the
    bundle key the section was authored under (``qc_threshold``) and re-labels to it, so a
    bad value loaded from YAML names the section the way callers and tests expect — keeping
    section/field semantics identical to the hand-rolled coercer (ADR 0028 / REP6 6c).
    """
    raise ConfigFieldError(section, exc.field, exc.value, exc.reason) from exc


def _build_section(cls: type, section: str, section_data: Any) -> Any:
    """Validate one section's raw mapping into its pydantic model.

    The section model is the whole schema: nested blocks (``grid``, ``stress_surface``,
    ``indices``, ``strike_selection``) are native nested models / ``dict[str, int]`` fields
    that pydantic builds from the YAML mapping, with the strict (``10.5 → int`` rejected),
    extra-forbid (unknown key rejected) and range/membership constraints all enforced in one
    pass. The model raises a structured :class:`ConfigFieldError` (its base class boundary);
    the loader re-labels its ``section`` to the bundle key here so a bad field names its
    section and field the same way the hand-rolled coercer used to (ADR 0028).
    """
    if not isinstance(section_data, Mapping):
        raise ConfigError(f"config section '{section}' must be a mapping")
    try:
        return cls(**_plain(section_data))
    except ConfigFieldError as exc:
        _relabel(section, exc)


def config_from_mapping(data: Mapping[str, Any]) -> PlatformConfig:
    """Build a validated config from a plain mapping (e.g. resolved YAML).

    Each economic section's raw mapping is handed to its pydantic model, which validates it
    (coerce by declared type, reject unknown/missing keys, enforce ranges/membership) and
    raises a labelled :class:`ConfigFieldError` naming the section and field on a bad value —
    so the YAML↔model schema cannot drift. ``data`` is keyed by section name (``universe``,
    ``qc_threshold``, ``solver``, ``surface``, ``forward``, ``scenario``, ``monetization``).
    """
    built: dict[str, Any] = {}
    for name, (cls, _filename, _subkey) in _PLATFORM_SECTIONS.items():
        if name not in data:
            raise ConfigError(f"config is missing required section '{name}'")
        built[name] = _build_section(cls, name, data[name])
    return PlatformConfig(**built)


def from_config(loaded: LoadedConfig) -> PlatformConfig:
    """Build a validated :class:`PlatformConfig` from a resolved YAML overlay config.

    The economic config is authored in versioned YAML and resolved through the overlay
    loader (``load_yaml_config`` — base + one overlay, deep-merged), then validated into
    the frozen pydantic models by the *same* ``config_from_mapping`` the bundle loader uses.
    This is the unified typed entry C7/ADR 0028 standardize on: one schema, one validation,
    the overlay loader's inheritance instead of a second untyped path.

    The required sections must be present in the resolved mapping; a missing one raises
    :class:`ConfigError`.
    """
    return config_from_mapping(dict(loaded.data))


def load_platform_config(configs_dir: str | Path) -> PlatformConfig:
    """Build the validated :class:`PlatformConfig` from the Part VII bundle files.

    Reads the economic bundles in ``configs_dir`` (``universe.yaml``, ``qc.yaml``,
    ``pricing.yaml``, ``scenarios.yaml``) — each authored per the blueprint Part VII
    taxonomy — and assembles them into the typed config through the one
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
