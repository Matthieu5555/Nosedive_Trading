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
from datetime import date, datetime
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


# The optional top-level key a bundle declares to date itself: the day from which the
# bundle's content is in force (ADR 0028 / TARGET §0 as-of resolution). It lives at the
# *file root*, beside the section blocks, never inside a section — the section models are
# ``extra="forbid"`` and own only economics, so the loader pops it before validation. A
# bundle without it is "current" (no as-of dimension), the zero-churn default.
_EFFECTIVE_FROM_KEY = "effective_from"


def _coerce_effective_from(label: str, value: Any) -> date:
    """Coerce a bundle's declared ``effective_from`` to a ``date``, or fail loudly.

    YAML parses an unquoted ``2026-01-01`` straight to a ``date`` (and a timestamp to a
    ``datetime``); a quoted value arrives as an ISO string. All three resolve to a plain
    ``date``; anything else — a number, a malformed string — is a config error naming the
    bundle, never a silent default (the ADR-0028 discipline for every config field).
    """
    # ``datetime`` is a subclass of ``date``, so test it first and take the calendar day.
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ConfigError(
                f"config bundle '{label}' has a malformed {_EFFECTIVE_FROM_KEY} "
                f"{value!r}: expected an ISO date (YYYY-MM-DD)"
            ) from exc
    raise ConfigError(
        f"config bundle '{label}' has a non-date {_EFFECTIVE_FROM_KEY} {value!r}: "
        f"expected an ISO date (YYYY-MM-DD)"
    )


def _guard_not_after(label: str, effective_from: date, as_of: date | None) -> None:
    """Reject a bundle whose ``effective_from`` is *after* the day being replayed.

    Replaying ``as_of`` must resolve the config that was in force *then*, not config
    authored later: a bundle effective after ``as_of`` did not exist on that day, so using
    it would be a look-ahead leak — the one place the platform trusts to be deterministic
    (TARGET §0: "provenance and versioning are your guarantee of no look-ahead cheating").
    A ``None`` ``as_of`` is the live "current" path and never guards.
    """
    if as_of is not None and effective_from > as_of:
        raise ConfigError(
            f"config bundle '{label}' is effective from {effective_from.isoformat()}, "
            f"after the as_of replay date {as_of.isoformat()}: replaying that day must "
            f"not pick up config authored later (look-ahead)"
        )


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


def from_config(loaded: LoadedConfig, as_of: date | None = None) -> PlatformConfig:
    """Build a validated :class:`PlatformConfig` from a resolved YAML overlay config.

    The economic config is authored in versioned YAML and resolved through the overlay
    loader (``load_yaml_config`` — base + one overlay, deep-merged), then validated into
    the frozen pydantic models by the *same* ``config_from_mapping`` the bundle loader uses.
    This is the unified typed entry C7/ADR 0028 standardize on: one schema, one validation,
    the overlay loader's inheritance instead of a second untyped path.

    ``as_of`` is the day being replayed: if the resolved config declares a top-level
    ``effective_from`` later than ``as_of``, the load is refused (look-ahead — see
    :func:`_guard_not_after`). ``None`` is the live "current" path and never guards. The
    ``effective_from`` key is metadata, not economics, so it is dropped before validation
    and never enters the typed config or its hash.

    The required sections must be present in the resolved mapping; a missing one raises
    :class:`ConfigError`.
    """
    mapping = dict(loaded.data)
    effective_from = mapping.pop(_EFFECTIVE_FROM_KEY, None)
    if effective_from is not None:
        _guard_not_after("<overlay>", _coerce_effective_from("<overlay>", effective_from), as_of)
    return config_from_mapping(mapping)


def load_platform_config(
    configs_dir: str | Path, as_of: date | None = None
) -> PlatformConfig:
    """Build the validated :class:`PlatformConfig` from the Part VII bundle files.

    Reads the economic bundles in ``configs_dir`` (``universe.yaml``, ``qc.yaml``,
    ``pricing.yaml``, ``scenarios.yaml``) — each authored per the blueprint Part VII
    taxonomy — and assembles them into the typed config through the one
    :func:`config_from_mapping` seam. A bundle may carry more than one section:
    ``pricing.yaml`` holds both ``solver:`` and ``surface:``. The operational bundles
    (``environment.yaml``, ``broker.yaml``) are not loaded here: they are not economics
    and must not enter the reproducibility hashes.

    ``as_of`` is the trading day being replayed. A bundle may date itself with a top-level
    ``effective_from``; when ``as_of`` is set, a bundle effective *after* it is refused —
    a replay of a past day must resolve the config in force *then*, never config authored
    later (the look-ahead guard, :func:`_guard_not_after`). A bundle without an
    ``effective_from``, or a ``None`` ``as_of``, is the "current" path: byte-identical to
    the load before this dimension existed (the ``effective_from`` key is metadata, popped
    before validation, so it never enters the typed config or its reproducibility hash).

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
                raw = dict(load_yaml_config(configs_dir / filename).data)
            except FileNotFoundError as exc:
                raise ConfigError(f"config bundle '{filename}' not found in {configs_dir}") from exc
            if _EFFECTIVE_FROM_KEY in raw:
                # Pop the dating metadata before validation (the section models forbid an
                # unknown key) and guard it against the replay date.
                _guard_not_after(
                    filename, _coerce_effective_from(filename, raw.pop(_EFFECTIVE_FROM_KEY)), as_of
                )
            files[filename] = raw
        contents = files[filename]
        if subkey is None:
            mapping[section] = dict(contents)
        else:
            if subkey not in contents:
                raise ConfigError(f"config bundle '{filename}' is missing the '{subkey}:' block")
            mapping[section] = dict(contents[subkey])
    return config_from_mapping(mapping)
