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

It also **freezes the fully-resolved config**: ``config_snapshot`` is the resolved config
mapping a run was built from, so the run replays from its own manifest alone — git is
dev-time only, the manifest is the run-time system of record (ADR 0028).
:func:`validate_manifest` is the gate that proves the snapshot and its hashes agree
(recompute-and-reject, the twin of ``validate_stamp``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class ManifestValidationError(Exception):
    """A manifest's frozen config snapshot does not match its stamped ``config_hashes``.

    Carries the offending bundle, the value seen, and a plain-language reason, so a
    rejection says exactly what was wrong with the manifest.
    """

    def __init__(self, bundle: str, value: object, reason: str) -> None:
        self.bundle = bundle
        self.value = value
        self.reason = reason
        super().__init__(f"manifest config_hashes[{bundle!r}]={value!r}: {reason}")


@dataclass(frozen=True)
class Manifest:
    """One job run's lineage: versions, the frozen config, partitions, status, correlation id."""

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
        """Serialize to a plain JSON-ready dict (the shape written to the manifest store)."""
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
    """Reject a manifest whose frozen config snapshot disagrees with its ``config_hashes``.

    The reproducibility gate for a run, mirroring ``provenance.validate_stamp``: a manifest
    with a ``config_snapshot`` must have ``config_hashes`` that equal a fresh recomputation
    from that snapshot, bundle for bundle — so a tampered or stale snapshot cannot pass as a
    faithful freeze. A manifest with no snapshot (older partitions, or a run that did not
    freeze one) is accepted as long as it carries at least one bundle hash. Raises
    :class:`ManifestValidationError` on the first mismatch.
    """
    if not manifest.config_hashes:
        raise ManifestValidationError("<all>", manifest.config_hashes, "must carry a bundle hash")
    if not manifest.config_snapshot:
        return
    # Imported lazily so the manifest dataclass stays import-light; both live in `core`.
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
