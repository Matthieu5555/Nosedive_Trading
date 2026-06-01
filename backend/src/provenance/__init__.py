"""Provenance stamping: deterministic lineage for every derived record."""

from __future__ import annotations

from .stamp import ProvenanceError, ProvenanceStamp, stamp

__all__ = ["ProvenanceError", "ProvenanceStamp", "stamp"]
