"""The run manifest: the lineage record every job emits.

A manifest answers, for one job run, "what produced these outputs?" — the code version, the
config versions (one hash per config bundle), the input partitions consumed, the output
partitions written, and a correlation id linking the run back to the collector session that
sourced its data. It is the small, durable artifact that makes the platform auditable and
replayable; ``to_dict`` serializes it for writing alongside the run's outputs.
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

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain JSON-ready dict (the shape written to the manifest store)."""
        return {
            "run_id": self.run_id,
            "environment": self.environment,
            "code_version": self.code_version,
            "config_hashes": dict(self.config_hashes),
            "input_partitions": dict(self.input_partitions),
            "output_partitions": dict(self.output_partitions),
            "status": self.status,
            "correlation_id": self.correlation_id,
        }
