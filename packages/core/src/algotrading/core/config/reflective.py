"""One reflective builder that turns a YAML mapping into a typed config dataclass.

The config standard (ADR 0028 / ``configuration-and-reproducibility.md``) mandates a
*single* ``from_config`` seam driven reflectively from the dataclass fields, rather than
each domain hand-listing its fields in a bespoke builder — that hand-listing is exactly
how the YAML↔dataclass schema drifts. :func:`build_dataclass` reflects over a frozen
dataclass's fields, coerces each value from the mapping by the field's declared type, and
constructs the instance (whose ``__post_init__`` does the range validation).

The failure contract is binding: a missing field, an unknown key, or a value that will
not coerce raises :class:`ConfigFieldError(section, field, value)` — never a bare
``KeyError``/``ValueError``, and never a silent default for an economic field.
"""

from __future__ import annotations

import dataclasses
import typing
from collections.abc import Mapping
from typing import Any


class ConfigFieldError(Exception):
    """A config field was missing, unknown, or out of range.

    Carries the ``section`` (the config bundle/dataclass name), the offending
    ``field``, the ``value`` seen, and a plain-language ``reason``, so a bad config
    names exactly what was wrong instead of failing deep inside with a bare
    ``KeyError``/``ValueError``.
    """

    def __init__(self, section: str, field: str, value: Any, reason: str = "") -> None:
        self.section = section
        self.field = field
        self.value = value
        self.reason = reason
        suffix = f": {reason}" if reason else ""
        super().__init__(f"config {section}.{field} = {value!r} is invalid{suffix}")


def _coerce(hint: Any, value: Any, section: str, field: str) -> Any:
    """Coerce ``value`` to the declared field type ``hint``, or raise ConfigFieldError."""
    origin = typing.get_origin(hint)
    if origin in (tuple, list):
        if not isinstance(value, (list, tuple)) or isinstance(value, (str, bytes)):
            raise ConfigFieldError(section, field, value, "expected a list")
        (item_hint, *_rest) = typing.get_args(hint) or (Any,)
        return tuple(_coerce(item_hint, item, section, field) for item in value)
    if hint is bool:
        if isinstance(value, bool):
            return value
        raise ConfigFieldError(section, field, value, "expected a boolean")
    if hint is int:
        # Reject bool (a bool where an int is declared is almost always a mistake) and
        # any non-integral float (10.5 for an int field is a config error, not a truncation).
        if isinstance(value, bool):
            raise ConfigFieldError(section, field, value, "expected an integer, got a boolean")
        try:
            coerced = int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigFieldError(section, field, value, f"not an integer ({exc})") from exc
        if isinstance(value, float) and coerced != value:
            raise ConfigFieldError(section, field, value, "expected an integer, got a fraction")
        return coerced
    if hint is float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigFieldError(section, field, value, f"not a number ({exc})") from exc
    if hint is str:
        if isinstance(value, (Mapping, list, tuple)):
            raise ConfigFieldError(section, field, value, "expected a string")
        return str(value)
    raise ConfigFieldError(section, field, value, f"unsupported field type {hint!r}")


def build_dataclass[T](cls: type[T], mapping: Mapping[str, Any], *, section: str) -> T:
    """Build a frozen config dataclass from ``mapping`` by reflecting over its fields.

    Every declared field must be present (no silent default for an economic field); every
    key in ``mapping`` must be a declared field (unknown keys are rejected, not ignored);
    each value is coerced to the field's declared type. Construction runs the dataclass's
    ``__post_init__`` validation, which raises :class:`ConfigFieldError` on a bad range.
    """
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")
    if not isinstance(mapping, Mapping):
        raise ConfigFieldError(section, "<root>", mapping, "expected a mapping")
    fields = dataclasses.fields(cls)
    field_names = {f.name for f in fields}
    unknown = set(mapping) - field_names
    if unknown:
        bad = sorted(unknown)[0]
        raise ConfigFieldError(section, bad, mapping[bad], "unknown key")
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for f in fields:
        if f.name not in mapping:
            raise ConfigFieldError(section, f.name, None, "missing required field")
        kwargs[f.name] = _coerce(hints[f.name], mapping[f.name], section, f.name)
    return cls(**kwargs)  # __post_init__ validates ranges and raises ConfigFieldError
