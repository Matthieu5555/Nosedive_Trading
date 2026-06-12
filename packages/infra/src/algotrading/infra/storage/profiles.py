"""Config profiles: the effective-dated, content-addressed record of a resolved config.

A **profile** is a named, point-in-time bundle of every compute parameter — a resolved
:class:`~algotrading.core.config.PlatformConfig` frozen to one immutable, content-addressed
version. It is the run-time form of the blueprint's config inheritance (ADR 0028): the
"now" stage (YAML overlays + per-run manifest freeze) lets a *run* replay from its own
manifest; this store adds the **as-of** stage — resolving "the config in force on day D" so
a *past day* replays through the config that was actually effective then, not today's.

Two discipline points make it trustworthy:

* **Content-addressed.** ``content_hash`` is the composite of the per-bundle config hashes,
  so editing a profile writes a *new* version (a new hash); a run pins an immutable hash and
  is never silently mutated. "What ran on D" and "what was in force on D" both have exact
  answers.
* **Effective-dated.** Each version carries ``effective_from``; :meth:`ProfileRepository.
  resolve_as_of` returns the latest version whose ``effective_from`` is on or before the
  queried date — the point-in-time discipline the platform already applies to market data
  and index membership.

The record carries the fully-resolved ``config_snapshot`` (rebuildable via
:func:`platform_config_from_profile`) and its ``config_hashes``, so a stored profile is
self-sufficient — git is dev-time only, this store is the run-time system of record.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from algotrading.core.config import (
    PlatformConfig,
    composite_config_hash,
    config_from_mapping,
    config_hashes,
    config_snapshot,
)
from pydantic import TypeAdapter
from pydantic.dataclasses import dataclass


@dataclass(frozen=True)
class ProfileVersion:
    """One immutable, effective-dated version of a named config profile.

    ``content_hash`` is the composite of ``config_hashes`` — the immutable handle a run
    pins. ``config_snapshot`` is the fully-resolved config (rebuildable into a
    :class:`PlatformConfig`); ``config_hashes`` are its per-bundle hashes.

    A pydantic dataclass: construction validates/coerces (an ISO string becomes a
    ``date``), so deserialization is one validation call. ``to_dict`` stays hand-written
    because its byte shape is persisted (repository payload columns) and pinned by a
    golden test.
    """

    name: str
    effective_from: date
    content_hash: str
    config_hashes: Mapping[str, str]
    config_snapshot: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain JSON-ready dict (the shape persisted by a repository)."""
        return {
            "name": self.name,
            "effective_from": self.effective_from.isoformat(),
            "content_hash": self.content_hash,
            "config_hashes": dict(self.config_hashes),
            "config_snapshot": dict(self.config_snapshot),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProfileVersion:
        """Rebuild a version from its serialized form (validated, coerced)."""
        return _PROFILE_VERSION_ADAPTER.validate_python(payload)


_PROFILE_VERSION_ADAPTER = TypeAdapter(ProfileVersion)


def build_profile_version(
    name: str, effective_from: date, config: PlatformConfig
) -> ProfileVersion:
    """Freeze a resolved config into an effective-dated, content-addressed profile version.

    The content hash is the composite of the per-bundle ``config_hashes`` (the folded
    convenience over the canonical per-bundle dict), so two configs that differ in any
    bundle get distinct versions, and re-freezing the same config is idempotent.
    """
    hashes = config_hashes(config)
    return ProfileVersion(
        name=name,
        effective_from=effective_from,
        content_hash=composite_config_hash(hashes),
        config_hashes=hashes,
        config_snapshot=config_snapshot(config),
    )


def platform_config_from_profile(version: ProfileVersion) -> PlatformConfig:
    """Rebuild the validated :class:`PlatformConfig` a profile version froze."""
    return config_from_mapping(dict(version.config_snapshot))
