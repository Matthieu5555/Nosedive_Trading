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

    name: str
    effective_from: date
    content_hash: str
    config_hashes: Mapping[str, str]
    config_snapshot: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "effective_from": self.effective_from.isoformat(),
            "content_hash": self.content_hash,
            "config_hashes": dict(self.config_hashes),
            "config_snapshot": dict(self.config_snapshot),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> ProfileVersion:
        return _PROFILE_VERSION_ADAPTER.validate_python(payload)


_PROFILE_VERSION_ADAPTER = TypeAdapter(ProfileVersion)


def build_profile_version(
    name: str, effective_from: date, config: PlatformConfig
) -> ProfileVersion:
    hashes = config_hashes(config)
    return ProfileVersion(
        name=name,
        effective_from=effective_from,
        content_hash=composite_config_hash(hashes),
        config_hashes=hashes,
        config_snapshot=config_snapshot(config),
    )


def platform_config_from_profile(version: ProfileVersion) -> PlatformConfig:
    return config_from_mapping(dict(version.config_snapshot))
