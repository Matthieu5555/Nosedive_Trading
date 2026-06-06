"""The run manifest: the lineage record every job emits.

A manifest answers, for one job run, "what produced these outputs?" — the code version,
the **code identity** (the exact VCS commit + dirty flag), the config versions (one hash
per config bundle), the input partitions consumed, the output partitions written, and a
correlation id linking the run back to the collector session that sourced its data. It is
the small, durable artifact that makes the platform auditable and replayable; ``to_dict``
serializes it for writing alongside the run's outputs.

``code_version`` (the installed distribution version) is necessary but not sufficient for
reproducibility — a dirty tree or a same-version edit defeats it — so the manifest also
carries ``code_identity`` (commit SHA + dirty flag), per ADR 0028.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Manifest:
    """One job run's lineage: versions, input/output partitions, status, and correlation id."""

    run_id: str
    environment: str
    code_version: str
    config_hashes: Mapping[str, str]
    input_partitions: Mapping[str, str]
    output_partitions: Mapping[str, str]
    status: str
    correlation_id: str | None = None
    code_identity: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain JSON-ready dict (the shape written to the manifest store)."""
        return {
            "run_id": self.run_id,
            "environment": self.environment,
            "code_version": self.code_version,
            "code_identity": self.code_identity,
            "config_hashes": dict(self.config_hashes),
            "input_partitions": dict(self.input_partitions),
            "output_partitions": dict(self.output_partitions),
            "status": self.status,
            "correlation_id": self.correlation_id,
        }
