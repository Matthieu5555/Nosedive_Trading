"""The provenance stamp every derived record carries.

A stamp answers "where did this number come from?" in one object: which source
records fed it, when they happened, when it was computed, which code version and
which config produced it. It is the mechanism behind two platform promises —
determinism (the same inputs always give the same stamp) and lineage (a derived
row points back at its raw inputs).

Two design choices make the determinism real rather than hoped-for:

* The source-record list and source-timestamp list are sorted into a canonical
  order when the stamp is built. So feeding the same sources in a different order
  yields a byte-identical stamp. Order of arrival is an accident of plumbing, not
  part of the result, so it must not change the result.

* The content hash is SHA-256 of canonical JSON, not Python's salted ``hash()``,
  so it is identical across processes and machines (see ``config`` for the same
  reasoning).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime


class ProvenanceError(Exception):
    """A stamp was asked to be built from invalid inputs (e.g. naive datetimes)."""


@dataclass(frozen=True, slots=True)
class ProvenanceStamp:
    """Immutable record of how a derived value was produced.

    Attributes:
        calc_ts: when the computation ran (timezone-aware).
        code_version: version string of the code that produced the value.
        config_hash: hash of the active config (links to exact settings).
        source_record_ids: ids of the source records used, in canonical order.
        source_timestamps: timestamps of those sources, in canonical order.
        stamp_hash: content hash of all of the above; the determinism handle.
    """

    calc_ts: datetime
    code_version: str
    config_hash: str
    source_record_ids: tuple[str, ...]
    source_timestamps: tuple[datetime, ...]
    stamp_hash: str


def _as_utc_iso(value: datetime) -> str:
    """Render a timezone-aware datetime as a UTC ISO string for hashing.

    Raises ``ProvenanceError`` on a naive datetime: a stamp with an ambiguous
    time is worse than no stamp, so we refuse to build one.
    """
    if value.tzinfo is None:
        raise ProvenanceError(f"naive datetime not allowed in a stamp: {value!r}")
    return value.astimezone(UTC).isoformat()


def stamp(
    *,
    calc_ts: datetime,
    code_version: str,
    config_hash: str,
    source_record_ids: tuple[str, ...],
    source_timestamps: tuple[datetime, ...],
) -> ProvenanceStamp:
    """Build a provenance stamp with a canonical, order-independent content hash.

    The source ids are sorted lexicographically and the source timestamps
    chronologically before anything is stored or hashed, so the resulting stamp
    does not depend on the order the caller passed them in.
    """
    sorted_ids = tuple(sorted(source_record_ids))
    sorted_ts = tuple(sorted(source_timestamps))
    payload = {
        "calc_ts": _as_utc_iso(calc_ts),
        "code_version": code_version,
        "config_hash": config_hash,
        "source_record_ids": list(sorted_ids),
        "source_timestamps": [_as_utc_iso(ts) for ts in sorted_ts],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    stamp_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ProvenanceStamp(
        calc_ts=calc_ts,
        code_version=code_version,
        config_hash=config_hash,
        source_record_ids=sorted_ids,
        source_timestamps=sorted_ts,
        stamp_hash=stamp_hash,
    )
