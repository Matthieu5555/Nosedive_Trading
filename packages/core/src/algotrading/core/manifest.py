from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class ManifestValidationError(Exception):

    def __init__(self, bundle: str, value: object, reason: str) -> None:
        self.bundle = bundle
        self.value = value
        self.reason = reason
        super().__init__(f"manifest config_hashes[{bundle!r}]={value!r}: {reason}")


@dataclass(frozen=True)
class Manifest:

    run_id: str
    environment: str
    code_version: str
    config_hashes: Mapping[str, str]
    input_partitions: Mapping[str, str]
    output_partitions: Mapping[str, str]
    status: str
    correlation_id: str | None = None
    code_identity: str = "unknown"
    config_snapshot: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "environment": self.environment,
            "code_version": self.code_version,
            "code_identity": self.code_identity,
            "config_hashes": dict(self.config_hashes),
            "config_snapshot": dict(self.config_snapshot),
            "input_partitions": dict(self.input_partitions),
            "output_partitions": dict(self.output_partitions),
            "status": self.status,
            "correlation_id": self.correlation_id,
        }


def validate_manifest(manifest: Manifest) -> None:
    if not manifest.config_hashes:
        raise ManifestValidationError("<all>", manifest.config_hashes, "must carry a bundle hash")
    if not manifest.config_snapshot:
        return
    from .config import config_from_mapping, config_hashes

    rebuilt = config_from_mapping(dict(manifest.config_snapshot))
    expected = config_hashes(rebuilt)
    stored = dict(manifest.config_hashes)
    if expected != stored:
        for bundle, digest in expected.items():
            if stored.get(bundle) != digest:
                raise ManifestValidationError(
                    bundle, stored.get(bundle), f"snapshot recomputes to {digest!r}"
                )
        raise ManifestValidationError(
            "<keys>", stored, f"snapshot covers only {sorted(expected)}"
        )
