"""The provenance stamp every derived record carries.

A stamp answers "where did this number come from?" in one object: which source
records fed it, when they happened, when it was computed, which code version and
which config produced it. It is the mechanism behind two platform promises —
determinism (the same inputs always give the same stamp) and lineage (a derived
row points back at its raw inputs).

A source is named by a :class:`SourceRecordRef`: its table plus its *full* primary
key, not a bare id. Raw events are identified by ``(session_id, event_id)``, so a
stamp that stored only ``event_id`` would conflate two sessions that happen to
share an event id. Carrying the whole key lets lineage resolve to exactly one row,
and lets a stamp point at any table — a snapshot, a forward point — not just raw
events.

Three design choices make the determinism real rather than hoped-for:

* The source-record list and source-timestamp list are sorted into a canonical
  order when the stamp is built. So feeding the same sources in a different order
  yields a byte-identical stamp. Order of arrival is an accident of plumbing, not
  part of the result, so it must not change the result.

* A reference's key components are stored as canonical strings (timestamps as UTC
  ISO, everything else via ``str``). That keeps a reference JSON-serializable for
  storage and keeps the content hash independent of how a key element happened to
  be typed in memory.

* The content hash is SHA-256 of canonical JSON, not Python's salted ``hash()``,
  so it is identical across processes and machines (see ``config`` for the same
  reasoning).

A stamp built through :func:`stamp` is valid by construction. A stamp that was
hand-built or mutated is not, so :func:`validate_stamp` is the gate that proves a
stamp is trustworthy — most importantly, that its stored hash still matches its
contents.

:func:`code_version` is the companion helper that reads the version of the
installed distribution a producer should stamp onto its outputs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from importlib import metadata

from .log import get_logger

_log = get_logger(__name__)

_FALLBACK_VERSION = "0.0.0+unknown"


class ProvenanceError(Exception):
    """A stamp was asked to be built from invalid inputs (e.g. naive datetimes)."""


class ProvenanceValidationError(ProvenanceError):
    """An existing stamp failed validation: ill-formed, or its hash does not match.

    Carries the offending field, the value that triggered it, and a plain-language
    reason, so a rejection says exactly what was wrong with the stamp.
    """

    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"provenance stamp {field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class SourceRecordRef:
    """A typed pointer to one source record: its table and full primary key.

    ``primary_key`` is the source table's complete key tuple, in the registry's
    key order, with each component reduced to its canonical string form (see
    :func:`canonical_primary_key`). Storing the whole key — not one field of it —
    is what lets lineage resolve to exactly the right row.
    """

    table: str
    primary_key: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceStamp:
    """Immutable record of how a derived value was produced.

    Attributes:
        calc_ts: when the computation ran (timezone-aware).
        code_version: version string of the code that produced the value.
        config_hash: hash of the active config (links to exact settings).
        source_records: typed references to the source records used, in canonical
            order.
        source_timestamps: timestamps of those sources, in canonical order.
        stamp_hash: content hash of all of the above; the determinism handle.
    """

    calc_ts: datetime
    code_version: str
    config_hash: str
    source_records: tuple[SourceRecordRef, ...]
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


def _canonical_component(value: object) -> str:
    """Reduce one primary-key element to its canonical string form.

    Timestamps become UTC ISO strings (and naive ones are refused, as everywhere
    in a stamp); dates become ISO dates; everything else is stringified. The point
    is that the same logical key always yields the same components, whether it was
    just built in memory or read back out of storage.
    """
    if isinstance(value, datetime):
        return _as_utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def canonical_primary_key(values: tuple[object, ...]) -> tuple[str, ...]:
    """Canonicalize a primary-key tuple to its string components.

    Used both when a producer builds a :class:`SourceRecordRef` and when storage
    resolves lineage, so the two agree on what a key "is" regardless of how its
    elements are typed in memory.
    """
    return tuple(_canonical_component(value) for value in values)


def source_ref(table: str, *key_values: object) -> SourceRecordRef:
    """Build a source reference from a table name and the source record's full key.

    Pass the key fields in the table's registry key order, e.g.
    ``source_ref("raw_market_events", session_id, event_id)``. Values are
    canonicalized to strings so the reference round-trips through storage.
    """
    return SourceRecordRef(table=table, primary_key=canonical_primary_key(key_values))


def _ref_payload(ref: SourceRecordRef) -> dict[str, object]:
    """The JSON-shaped, hash-stable form of one source reference."""
    return {"table": ref.table, "primary_key": list(ref.primary_key)}


def _ref_sort_key(ref: SourceRecordRef) -> str:
    """A total, deterministic ordering key for references (their canonical JSON)."""
    return json.dumps(_ref_payload(ref), sort_keys=True, separators=(",", ":"))


def _sorted_sources(
    source_records: tuple[SourceRecordRef, ...],
    source_timestamps: tuple[datetime, ...],
) -> tuple[tuple[SourceRecordRef, ...], tuple[datetime, ...]]:
    """Put both source lists in canonical order, so input order cannot leak in."""
    return (
        tuple(sorted(source_records, key=_ref_sort_key)),
        tuple(sorted(source_timestamps)),
    )


def _canonical_stamp_hash(
    *,
    calc_ts: datetime,
    code_version: str,
    config_hash: str,
    source_records: tuple[SourceRecordRef, ...],
    source_timestamps: tuple[datetime, ...],
) -> str:
    """SHA-256 of the canonical JSON of a stamp's contents.

    The source lists are expected already in canonical order (see
    :func:`_sorted_sources`); this function only renders and hashes them.
    """
    payload = {
        "calc_ts": _as_utc_iso(calc_ts),
        "code_version": code_version,
        "config_hash": config_hash,
        "source_records": [_ref_payload(ref) for ref in source_records],
        "source_timestamps": [_as_utc_iso(ts) for ts in source_timestamps],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stamp(
    *,
    calc_ts: datetime,
    code_version: str,
    config_hash: str,
    source_records: tuple[SourceRecordRef, ...],
    source_timestamps: tuple[datetime, ...],
) -> ProvenanceStamp:
    """Build a provenance stamp with a canonical, order-independent content hash.

    The source references and source timestamps are sorted into canonical order
    before anything is stored or hashed, so the resulting stamp does not depend on
    the order the caller passed them in.
    """
    sorted_records, sorted_ts = _sorted_sources(source_records, source_timestamps)
    stamp_hash = _canonical_stamp_hash(
        calc_ts=calc_ts,
        code_version=code_version,
        config_hash=config_hash,
        source_records=sorted_records,
        source_timestamps=sorted_ts,
    )
    return ProvenanceStamp(
        calc_ts=calc_ts,
        code_version=code_version,
        config_hash=config_hash,
        source_records=sorted_records,
        source_timestamps=sorted_ts,
        stamp_hash=stamp_hash,
    )


def validate_stamp(candidate: ProvenanceStamp) -> None:
    """Reject a provenance stamp that is ill-formed or whose hash does not match.

    A stamp built by :func:`stamp` always passes; this gate exists for stamps that
    were hand-constructed, mutated, or read back from an untrusted source. The
    load-bearing check is the last one: the stored ``stamp_hash`` must equal a
    fresh recomputation from the stamp's own contents, so a tampered field cannot
    pass unnoticed. Raises :class:`ProvenanceValidationError` on the first failure.
    """
    if not isinstance(candidate, ProvenanceStamp):
        raise ProvenanceValidationError("stamp", candidate, "must be a ProvenanceStamp")
    if candidate.calc_ts.tzinfo is None:
        raise ProvenanceValidationError("calc_ts", candidate.calc_ts, "must be timezone-aware")
    for field_name in ("code_version", "config_hash", "stamp_hash"):
        value = getattr(candidate, field_name)
        if not value:
            raise ProvenanceValidationError(field_name, value, "must be non-empty")
    for source_ts in candidate.source_timestamps:
        if source_ts.tzinfo is None:
            raise ProvenanceValidationError(
                "source_timestamps", source_ts, "every source timestamp must be timezone-aware"
            )
    for ref in candidate.source_records:
        if not isinstance(ref, SourceRecordRef) or not ref.table or not ref.primary_key:
            raise ProvenanceValidationError(
                "source_records",
                ref,
                "each source reference needs a table and a non-empty primary key",
            )
    sorted_records, sorted_ts = _sorted_sources(
        candidate.source_records, candidate.source_timestamps
    )
    expected = _canonical_stamp_hash(
        calc_ts=candidate.calc_ts,
        code_version=candidate.code_version,
        config_hash=candidate.config_hash,
        source_records=sorted_records,
        source_timestamps=sorted_ts,
    )
    if expected != candidate.stamp_hash:
        raise ProvenanceValidationError(
            "stamp_hash",
            candidate.stamp_hash,
            f"does not match the recomputed canonical hash {expected!r}",
        )


def code_version(distribution: str) -> str:
    """Return the installed version of ``distribution``, or a labelled fallback if absent.

    Each caller passes its own distribution name (e.g. ``"algotrading-infra"``), so a
    stored result records the exact code that produced it. A bare checkout where the
    distribution is not installed yields a labelled fallback rather than failing silently.
    """
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        _log.warning("distribution %s not found; using fallback version", distribution)
        return _FALLBACK_VERSION
