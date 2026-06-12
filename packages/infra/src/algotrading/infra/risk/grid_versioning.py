"""Shared grid-construction identity helpers for the scenario and stress-surface grids.

Both grid builders (:mod:`.scenarios`, :mod:`.stress_surface`) need the same two
primitives, previously copy-pasted into each module:

* an order-preserving de-dup of configured shock values, so duplicate shocks never mint
  colliding scenario ids that silently collapse cells in an id-keyed map;
* a short, cross-process-stable hash of the grid-construction constants, folded into the
  persisted ``effective_*_version`` strings so two different grids can never share one
  version.

The hash is SHA-256 over canonical JSON (sorted keys, compact separators) — never
Python's salted ``hash()`` — truncated to a configurable prefix (12 hex chars today,
the format every persisted ``effective_*_version`` string already carries; byte-identical
to the inlined copies this module replaced). The payloads themselves stay at the call
sites: this module owns the *encoding*, each grid owns *what* identifies it.

Boundary note: ``core`` is growing a shared hashing module (M25) with the same
canonical-JSON-SHA256 primitive; when it lands, the body of
:func:`short_construction_hash` should delegate to it (same bytes, gated by the
version-string pins in ``test_scenario.py`` / ``test_risk_surface.py``).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

# The persisted version strings carry a 12-hex-char construction-hash suffix; this is the
# format constant, not a tunable (changing it would move every effective_*_version).
_SHORT_HASH_LENGTH = 12


def dedup_preserving_order(values: tuple[float, ...]) -> tuple[float, ...]:
    """Drop duplicate shock values, keeping first-seen order — a deterministic de-dup.

    Duplicate configured shocks (or axis points that collapse after rounding) would
    otherwise mint duplicate scenario ids, which silently merge cells in any id-keyed
    map and double-count a scenario in a worst-case total. De-duping at the source keeps
    every grid well-formed regardless of config hygiene.
    """
    seen: set[float] = set()
    unique: list[float] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return tuple(unique)


def short_construction_hash(
    payload: Mapping[str, object], *, length: int = _SHORT_HASH_LENGTH
) -> str:
    """A short, stable hash of a grid-construction payload, for ``effective_*_version``.

    SHA-256 over canonical JSON (sorted keys, ``(",", ":")`` separators), truncated to
    ``length`` hex characters — identical on every machine, in every run, forever, unlike
    the per-process-salted built-in ``hash()``. The caller builds the payload naming
    everything that identifies its grid construction (policy version, axis rules, …);
    any change to that payload moves the persisted version automatically.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:length]
