"""The canonical-JSON + SHA-256 primitives every content hash is built from.

Reproducibility hashes across the platform (provenance stamp hashes, config bundle
hashes, grid-construction version suffixes) all reduce to the same two operations:
render a payload as canonical JSON, then take its SHA-256. Before this module each
call site re-typed that idiom by hand, and the copies had silently diverged into
*different* definitions of "canonical JSON" (M25). This module is the single
reviewed home of the encoding and the digest; any future hash-relevant change
happens here, in one place, gated by the golden-hash pins in the test suites.

The repo deliberately keeps **three named conventions** — they feed *persisted*
hashes, so they must not be unified numerically (that would move every stored
digest):

* the **bare** convention — :func:`canonical_dumps` here: ``sort_keys=True``,
  compact separators, values verbatim (``-0.0`` stays ``-0.0``, NaN allowed by
  ``json``'s default). Used by the provenance stamp hash, the composite config
  hash, the risk grids' construction hashes, and the QC context serialization.
* the **typed-config** convention — ``core.config.canonical_json``: a structural
  pre-pass (pydantic models/dataclasses to field mappings, ``-0.0`` collapsed onto
  ``0.0``) plus ``allow_nan=False``. Keeps its explicit name in ``core.config``.
* the **yaml-loader** convention — ``core.config.mapping_config_hash``:
  stringified keys, ``-0.0`` collapsed, ``default=str``, ``allow_nan=False``.

Both config variants delegate their digest step to :func:`sha256_hex`, so the
hash function itself has exactly one home even where the encodings differ.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_dumps(value: Any) -> str:
    """Render ``value`` as canonical JSON under the bare convention.

    Sorted keys and compact ``(",", ":")`` separators make the output a pure
    function of the payload's *contents*, never of dict construction order — the
    property every content hash relies on. Values are serialized verbatim: no
    ``-0.0`` collapse, no ``default=`` fallback. Byte-for-byte identical to the
    inlined ``json.dumps(payload, sort_keys=True, separators=(",", ":"))`` copies
    this function replaced (pinned by golden-hash tests).
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_hex(text: str) -> str:
    """The full SHA-256 hex digest of ``text`` (UTF-8 encoded).

    SHA-256, never Python's per-process-salted ``hash()``, so the digest is
    identical across processes and machines. Callers that persist a short form
    truncate the hex themselves (e.g. the risk grids' 12-char version suffix).
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
