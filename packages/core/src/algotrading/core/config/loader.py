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
    pass


_EFFECTIVE_FROM_KEY = "effective_from"


def _coerce_effective_from(label: str, value: Any) -> date:
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
    if as_of is not None and effective_from > as_of:
        raise ConfigError(
            f"config bundle '{label}' is effective from {effective_from.isoformat()}, "
            f"after the as_of replay date {as_of.isoformat()}: replaying that day must "
            f"not pick up config authored later (look-ahead)"
        )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _relabel(section: str, exc: ConfigFieldError) -> NoReturn:
    raise ConfigFieldError(section, exc.field, exc.value, exc.reason) from exc


def _build_section(cls: type, section: str, section_data: Any) -> Any:
    if not isinstance(section_data, Mapping):
        raise ConfigError(f"config section '{section}' must be a mapping")
    try:
        return cls(**_plain(section_data))
    except ConfigFieldError as exc:
        _relabel(section, exc)


def config_from_mapping(data: Mapping[str, Any]) -> PlatformConfig:
    built: dict[str, Any] = {}
    for name, (cls, _filename, _subkey) in _PLATFORM_SECTIONS.items():
        if name not in data:
            raise ConfigError(f"config is missing required section '{name}'")
        built[name] = _build_section(cls, name, data[name])
    return PlatformConfig(**built)


def from_config(loaded: LoadedConfig, as_of: date | None = None) -> PlatformConfig:
    mapping = dict(loaded.data)
    effective_from = mapping.pop(_EFFECTIVE_FROM_KEY, None)
    if effective_from is not None:
        _guard_not_after("<overlay>", _coerce_effective_from("<overlay>", effective_from), as_of)
    return config_from_mapping(mapping)


def load_platform_config(
    configs_dir: str | Path, as_of: date | None = None
) -> PlatformConfig:
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
