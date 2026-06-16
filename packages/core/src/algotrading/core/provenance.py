from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from importlib import metadata

from .hashing import canonical_dumps, sha256_hex
from .log import get_logger

_log = get_logger(__name__)

_FALLBACK_VERSION = "0.0.0+unknown"


class ProvenanceError(Exception):
    pass


class ProvenanceValidationError(ProvenanceError):

    def __init__(self, field: str, value: object, reason: str) -> None:
        self.field = field
        self.value = value
        self.reason = reason
        super().__init__(f"provenance stamp {field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class SourceRecordRef:

    table: str
    primary_key: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProvenanceStamp:

    calc_ts: datetime
    code_version: str
    config_hashes: Mapping[str, str] = field(hash=False)
    source_records: tuple[SourceRecordRef, ...]
    source_timestamps: tuple[datetime, ...]
    stamp_hash: str
    as_of: date | None = None


def _as_utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ProvenanceError(f"naive datetime not allowed in a stamp: {value!r}")
    return value.astimezone(UTC).isoformat()


def _canonical_component(value: object) -> str:
    if isinstance(value, datetime):
        return _as_utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def canonical_primary_key(values: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(_canonical_component(value) for value in values)


def source_ref(table: str, *key_values: object) -> SourceRecordRef:
    return SourceRecordRef(table=table, primary_key=canonical_primary_key(key_values))


def _ref_payload(ref: SourceRecordRef) -> dict[str, object]:
    return {"table": ref.table, "primary_key": list(ref.primary_key)}


def _ref_sort_key(ref: SourceRecordRef) -> str:
    return canonical_dumps(_ref_payload(ref))


def _sorted_sources(
    source_records: tuple[SourceRecordRef, ...],
    source_timestamps: tuple[datetime, ...],
) -> tuple[tuple[SourceRecordRef, ...], tuple[datetime, ...]]:
    return (
        tuple(sorted(source_records, key=_ref_sort_key)),
        tuple(sorted(source_timestamps)),
    )


def _canonical_stamp_hash(
    *,
    calc_ts: datetime,
    code_version: str,
    config_hashes: Mapping[str, str],
    source_records: tuple[SourceRecordRef, ...],
    source_timestamps: tuple[datetime, ...],
    as_of: date | None = None,
) -> str:
    payload = {
        "calc_ts": _as_utc_iso(calc_ts),
        "code_version": code_version,
        "config_hashes": dict(config_hashes),
        "source_records": [_ref_payload(ref) for ref in source_records],
        "source_timestamps": [_as_utc_iso(ts) for ts in source_timestamps],
    }
    if as_of is not None:
        payload["as_of"] = as_of.isoformat()
    return sha256_hex(canonical_dumps(payload))


def stamp(
    *,
    calc_ts: datetime,
    code_version: str,
    config_hashes: Mapping[str, str],
    source_records: tuple[SourceRecordRef, ...],
    source_timestamps: tuple[datetime, ...],
    as_of: date | None = None,
) -> ProvenanceStamp:
    sorted_records, sorted_ts = _sorted_sources(source_records, source_timestamps)
    frozen_hashes = dict(config_hashes)
    stamp_hash = _canonical_stamp_hash(
        calc_ts=calc_ts,
        code_version=code_version,
        config_hashes=frozen_hashes,
        source_records=sorted_records,
        source_timestamps=sorted_ts,
        as_of=as_of,
    )
    return ProvenanceStamp(
        calc_ts=calc_ts,
        code_version=code_version,
        config_hashes=frozen_hashes,
        source_records=sorted_records,
        source_timestamps=sorted_ts,
        stamp_hash=stamp_hash,
        as_of=as_of,
    )


def snapshot_stamp(
    *,
    calc_ts: datetime,
    code_version: str,
    config_hashes: Mapping[str, str],
    source_snapshot_ts: datetime,
    source_records: tuple[SourceRecordRef, ...],
    as_of: date | None = None,
) -> ProvenanceStamp:
    return stamp(
        calc_ts=calc_ts,
        code_version=code_version,
        config_hashes=config_hashes,
        source_records=source_records,
        source_timestamps=tuple(source_snapshot_ts for _ in source_records),
        as_of=as_of,
    )


def validate_stamp(candidate: ProvenanceStamp) -> None:
    if not isinstance(candidate, ProvenanceStamp):
        raise ProvenanceValidationError("stamp", candidate, "must be a ProvenanceStamp")
    if candidate.calc_ts.tzinfo is None:
        raise ProvenanceValidationError("calc_ts", candidate.calc_ts, "must be timezone-aware")
    for field_name in ("code_version", "stamp_hash"):
        value = getattr(candidate, field_name)
        if not value:
            raise ProvenanceValidationError(field_name, value, "must be non-empty")
    if not candidate.config_hashes:
        raise ProvenanceValidationError(
            "config_hashes", candidate.config_hashes, "must carry at least one bundle hash"
        )
    for bundle, digest in candidate.config_hashes.items():
        if not bundle or not digest:
            raise ProvenanceValidationError(
                "config_hashes", {bundle: digest}, "every bundle and hash must be non-empty"
            )
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
        config_hashes=candidate.config_hashes,
        source_records=sorted_records,
        source_timestamps=sorted_ts,
        as_of=candidate.as_of,
    )
    if expected != candidate.stamp_hash:
        raise ProvenanceValidationError(
            "stamp_hash",
            candidate.stamp_hash,
            f"does not match the recomputed canonical hash {expected!r}",
        )


def code_version(distribution: str) -> str:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        _log.warning("distribution %s not found; using fallback version", distribution)
        return _FALLBACK_VERSION


_UNKNOWN_CODE_IDENTITY = "unknown"


def code_identity() -> str:
    import subprocess

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, check=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        _log.warning("git code identity unavailable; using fallback")
        return _UNKNOWN_CODE_IDENTITY
    if not sha:
        return _UNKNOWN_CODE_IDENTITY
    return f"{sha}-dirty" if dirty else sha
