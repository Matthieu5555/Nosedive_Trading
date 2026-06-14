"""The booking audit log: an append-only, provenance-stamped record of every commit/block.

TARGET §6 requires "an append-only audit log" of the booking decisions. Every call through the
password-gated write barrier (:func:`~.commit.book`) writes exactly one :class:`BookingAudit`
record here — a commit *or* a labelled block — so the log is the complete, ordered, tamper-evident
history of every attempt to mutate the book, including the refusals.

This is the *decision* log, distinct from the fills ledger (:mod:`~..ledger`): the ledger holds
the fills a *commit* produced (nothing on a block), while this log holds *every* decision. They
share the same append-only discipline (blueprint Part XV/XIX): a record, once appended, is
immutable; re-appending a known ``audit_id`` is a labelled rejection; there is **no** update or
delete verb — a correction is a new record, never a mutation of a past one.

Two implementations share the invariants behind :class:`BookingAuditLog`: an in-memory store and a
durable JSONL store (one canonical-JSON line per record, a file that only grows, replayed on
restart), mirroring the fills ledger. The provenance stamp is validated at the **append door**, so
a hand-built or tampered stamp cannot enter the log; the stamp is order-independent, which is what
makes a replay of the decision sequence reorder-stable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from algotrading.core.hashing import canonical_dumps
from algotrading.core.provenance import ProvenanceStamp, SourceRecordRef, validate_stamp

COMMIT = "commit"
BLOCK = "block"
_DECISIONS = (COMMIT, BLOCK)


class BookingAuditError(Exception):
    """A labelled rejection from the audit-log door (a duplicate id, a malformed record)."""

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class BookingAudit:
    """One immutable record of a booking commit/block decision.

    ``decision`` is :data:`COMMIT` or :data:`BLOCK`. ``block_reason`` names *why* a block was
    blocked (the gate's ``wrong_password``/``absent_password``/… or ``unresolvable_leg``) and is
    ``None`` on a commit. ``fill_ids`` lists the fills a commit appended to the ledger (empty on
    a block — a block writes no fill). ``booking_id`` ties the audit record to the fills it
    produced (a fill's ``booking_id`` equals this), and ``source_basket_id`` stamps the
    originating intention. ``provenance`` makes the decision replayable and reorder-stable; it is
    validated at the append door, not here (this contract validates its own scalar fields).
    """

    audit_id: str
    booking_id: str
    source_basket_id: str
    trade_date: date
    underlying: str
    decision: str
    fill_ids: tuple[str, ...]
    decision_ts: datetime
    provenance: ProvenanceStamp
    block_reason: str | None = None

    def __post_init__(self) -> None:
        for name in ("audit_id", "booking_id", "source_basket_id", "underlying"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise BookingAuditError("must be a non-empty string", field=name, value=value)
        if self.decision not in _DECISIONS:
            raise BookingAuditError(
                f"must be one of {_DECISIONS}", field="decision", value=self.decision
            )
        if self.decision == BLOCK and not (self.block_reason and self.block_reason.strip()):
            raise BookingAuditError(
                "a block must name its reason", field="block_reason", value=self.block_reason
            )
        if self.decision == COMMIT and self.block_reason is not None:
            raise BookingAuditError(
                "a commit carries no block reason", field="block_reason", value=self.block_reason
            )
        if self.decision == COMMIT and not self.fill_ids:
            raise BookingAuditError(
                "a commit records the fills it wrote", field="fill_ids", value=self.fill_ids
            )
        if self.decision == BLOCK and self.fill_ids:
            raise BookingAuditError(
                "a block writes no fill", field="fill_ids", value=self.fill_ids
            )
        if self.decision_ts.tzinfo is None:
            raise BookingAuditError(
                "must be timezone-aware", field="decision_ts", value=self.decision_ts
            )


def _matches(record: BookingAudit, *, trade_date: date | None, underlying: str | None) -> bool:
    if trade_date is not None and record.trade_date != trade_date:
        return False
    return not (underlying is not None and record.underlying != underlying)


def _validated(record: BookingAudit, *, seen: frozenset[str]) -> None:
    """Append-door checks: a BookingAudit instance, a valid stamp, a fresh audit_id."""
    if not isinstance(record, BookingAudit):
        raise BookingAuditError("must be a BookingAudit", field="record", value=record)
    validate_stamp(record.provenance)
    if record.audit_id in seen:
        raise BookingAuditError(
            "a record with this id is already in the log (append-only: no overwrite)",
            field="audit_id",
            value=record.audit_id,
        )


@runtime_checkable
class BookingAuditLog(Protocol):
    """The append-only booking-decision log the commit verb writes to.

    An implementation must reject a duplicate ``audit_id`` and offer no mutate/delete verb.
    ``read`` returns records in append order, optionally narrowed to one trade date / underlying.
    """

    def append(self, record: BookingAudit) -> None: ...

    def read(
        self, *, trade_date: date | None = None, underlying: str | None = None
    ) -> tuple[BookingAudit, ...]: ...


class InMemoryBookingAuditLog:
    """An append-only booking-decision log held in memory — the working store."""

    def __init__(self) -> None:
        self._records: list[BookingAudit] = []
        self._ids: set[str] = set()

    def append(self, record: BookingAudit) -> None:
        _validated(record, seen=frozenset(self._ids))
        self._records.append(record)
        self._ids.add(record.audit_id)

    def read(
        self, *, trade_date: date | None = None, underlying: str | None = None
    ) -> tuple[BookingAudit, ...]:
        return tuple(
            r for r in self._records if _matches(r, trade_date=trade_date, underlying=underlying)
        )


class JsonlBookingAuditLog:
    """A durable append-only booking-decision log: one canonical-JSON line per record.

    The backing file only ever grows — :meth:`append` opens it in append mode and writes a
    single line; there is no rewrite path, so the file *is* the audit trail. On construction the
    existing file is replayed to recover the known ids (so a duplicate is rejected across
    restarts) and the in-order contents. Serialization is canonical (sorted keys, UTC-ISO
    timestamps) so two identical records produce byte-identical lines.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._records: list[BookingAudit] = []
        self._ids: set[str] = set()
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = _audit_from_jsonable(json.loads(line))
                self._records.append(record)
                self._ids.add(record.audit_id)

    def append(self, record: BookingAudit) -> None:
        _validated(record, seen=frozenset(self._ids))
        line = canonical_dumps(_audit_to_jsonable(record))
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self._records.append(record)
        self._ids.add(record.audit_id)

    def read(
        self, *, trade_date: date | None = None, underlying: str | None = None
    ) -> tuple[BookingAudit, ...]:
        return tuple(
            r for r in self._records if _matches(r, trade_date=trade_date, underlying=underlying)
        )


# --- JSONL serialization ------------------------------------------------------------------
# An audit record carries two dates/timestamps, a tuple of ids, and a nested ProvenanceStamp.
# Each is reduced to a JSON-stable scalar and rebuilt faithfully so a round-trip preserves the
# stamp hash (validate_stamp passes on the way back in).


def _stamp_to_jsonable(prov: ProvenanceStamp) -> dict[str, object]:
    return {
        "calc_ts": prov.calc_ts.isoformat(),
        "code_version": prov.code_version,
        "config_hashes": dict(prov.config_hashes),
        "source_records": [
            {"table": ref.table, "primary_key": list(ref.primary_key)}
            for ref in prov.source_records
        ],
        "source_timestamps": [ts.isoformat() for ts in prov.source_timestamps],
        "stamp_hash": prov.stamp_hash,
    }


def _stamp_from_jsonable(payload: Any) -> ProvenanceStamp:
    return ProvenanceStamp(
        calc_ts=datetime.fromisoformat(str(payload["calc_ts"])),
        code_version=str(payload["code_version"]),
        config_hashes={str(k): str(v) for k, v in payload["config_hashes"].items()},
        source_records=tuple(
            SourceRecordRef(
                table=str(r["table"]),
                primary_key=tuple(str(k) for k in r["primary_key"]),
            )
            for r in payload["source_records"]
        ),
        source_timestamps=tuple(
            datetime.fromisoformat(str(ts)) for ts in payload["source_timestamps"]
        ),
        stamp_hash=str(payload["stamp_hash"]),
    )


def _audit_to_jsonable(record: BookingAudit) -> dict[str, object]:
    return {
        "audit_id": record.audit_id,
        "booking_id": record.booking_id,
        "source_basket_id": record.source_basket_id,
        "trade_date": record.trade_date.isoformat(),
        "underlying": record.underlying,
        "decision": record.decision,
        "fill_ids": list(record.fill_ids),
        "decision_ts": record.decision_ts.isoformat(),
        "block_reason": record.block_reason,
        "provenance": _stamp_to_jsonable(record.provenance),
    }


def _audit_from_jsonable(payload: Any) -> BookingAudit:
    try:
        return BookingAudit(
            audit_id=str(payload["audit_id"]),
            booking_id=str(payload["booking_id"]),
            source_basket_id=str(payload["source_basket_id"]),
            trade_date=date.fromisoformat(str(payload["trade_date"])),
            underlying=str(payload["underlying"]),
            decision=str(payload["decision"]),
            fill_ids=tuple(str(fid) for fid in payload["fill_ids"]),
            decision_ts=datetime.fromisoformat(str(payload["decision_ts"])),
            provenance=_stamp_from_jsonable(payload["provenance"]),
            block_reason=(
                None if payload["block_reason"] is None else str(payload["block_reason"])
            ),
        )
    except (KeyError, ValueError, BookingAuditError) as exc:
        raise BookingAuditError(
            f"malformed audit record on read: {exc}", field="record", value=payload
        ) from exc
