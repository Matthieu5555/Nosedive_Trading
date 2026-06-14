"""The fills ledger: the append-only, auditable source of record for accounting from fills.

Blueprint governance (Part XV/XIX): "No downstream layer may silently overwrite an upstream
observation"; "Any replay or backfill must write a new version identifier instead of silently
mutating past results." A fills ledger is the purest case of that rule — a fill is an
*observation* of an execution, so the ledger is **append-only** by construction:

* a fill, once appended, is immutable;
* re-appending a known ``fill_id`` is a labelled rejection (no silent overwrite);
* there is **no** update or delete verb on the contract — a correction is a new
  (compensating) fill, never a mutation of a past one.

Two implementations share these invariants behind the :class:`FillsLedger` protocol: an
:class:`InMemoryFillsLedger` (the working store, used in tests and by pure callers) and a
:class:`JsonlFillsLedger` (durable — one canonical-JSON line per fill, a file that only ever
grows, replayable on restart). Reads filter by ``(trade_date, underlying)`` and return fills
in append order, so a replay reconstructs the booking sequence stably.

The provenance stamp on each fill is validated at the **append door** (the storage-boundary
convention: typed objects are checked before any bytes are written), so a tampered or
hand-built stamp cannot enter the ledger.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from algotrading.core.hashing import canonical_dumps
from algotrading.core.provenance import ProvenanceStamp, SourceRecordRef, validate_stamp

from .fills import Fill, FillError


class FillsLedgerError(Exception):
    """A labelled rejection from the ledger door (a duplicate fill_id, a malformed record).

    Carries the offending ``field``/``value`` and a human ``reason`` so an audit reader sees
    exactly which invariant was violated.
    """

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@runtime_checkable
class FillsLedger(Protocol):
    """The append-only fills store risk and attribution read from.

    An implementation must reject a duplicate ``fill_id`` and must offer no mutate/delete
    verb. ``read`` returns fills in append order, optionally narrowed to one trade date and/or
    underlying.
    """

    def append(self, fill: Fill) -> None: ...

    def append_many(self, fills: Iterable[Fill]) -> None: ...

    def read(
        self, *, trade_date: date | None = None, underlying: str | None = None
    ) -> tuple[Fill, ...]: ...


def _validated(fill: Fill, *, seen: frozenset[str]) -> None:
    """Run the append-door checks for one fill: a Fill instance, a valid stamp, a fresh id."""
    if not isinstance(fill, Fill):
        raise FillsLedgerError("must be a Fill", field="fill", value=fill)
    # The Fill contract already validated its scalar fields at construction; the stamp is the
    # one field it deliberately leaves to the door, so it is checked here.
    validate_stamp(fill.provenance)
    if fill.fill_id in seen:
        raise FillsLedgerError(
            "a fill with this id is already in the ledger (append-only: no overwrite)",
            field="fill_id",
            value=fill.fill_id,
        )


def _matches(fill: Fill, *, trade_date: date | None, underlying: str | None) -> bool:
    if trade_date is not None and fill.trade_date != trade_date:
        return False
    return not (underlying is not None and fill.underlying != underlying)


class InMemoryFillsLedger:
    """An append-only fills ledger held in memory — the working store.

    Append order is preserved; a duplicate ``fill_id`` is rejected; there is no verb that
    mutates or removes a stored fill.
    """

    def __init__(self) -> None:
        self._fills: list[Fill] = []
        self._ids: set[str] = set()

    def append(self, fill: Fill) -> None:
        _validated(fill, seen=frozenset(self._ids))
        self._fills.append(fill)
        self._ids.add(fill.fill_id)

    def append_many(self, fills: Iterable[Fill]) -> None:
        # Append one at a time so a duplicate inside the batch is caught against the ids
        # already taken earlier in the same batch, not only against the prior contents.
        for fill in fills:
            self.append(fill)

    def read(
        self, *, trade_date: date | None = None, underlying: str | None = None
    ) -> tuple[Fill, ...]:
        return tuple(
            f for f in self._fills if _matches(f, trade_date=trade_date, underlying=underlying)
        )


class JsonlFillsLedger:
    """A durable append-only fills ledger: one canonical-JSON line per fill.

    The backing file only ever grows — :meth:`append` opens it in append mode and writes a
    single line; there is no rewrite path, so the file *is* the audit trail. On construction
    the existing file is replayed to recover the set of known ids (so a duplicate is rejected
    across restarts) and the in-order contents. Serialization is canonical (sorted keys,
    UTC-ISO timestamps) so two identical fills produce byte-identical lines.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._fills: list[Fill] = []
        self._ids: set[str] = set()
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                fill = _fill_from_jsonable(json.loads(line))
                self._fills.append(fill)
                self._ids.add(fill.fill_id)

    def append(self, fill: Fill) -> None:
        _validated(fill, seen=frozenset(self._ids))
        line = canonical_dumps(_fill_to_jsonable(fill))
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self._fills.append(fill)
        self._ids.add(fill.fill_id)

    def append_many(self, fills: Iterable[Fill]) -> None:
        for fill in fills:
            self.append(fill)

    def read(
        self, *, trade_date: date | None = None, underlying: str | None = None
    ) -> tuple[Fill, ...]:
        return tuple(
            f for f in self._fills if _matches(f, trade_date=trade_date, underlying=underlying)
        )


# --- JSONL serialization ------------------------------------------------------------------
# A fill carries a Decimal, two dates/timestamps, and a nested ProvenanceStamp. Each is
# reduced to a JSON-stable scalar and rebuilt faithfully so a round-trip preserves the stamp
# hash (validate_stamp passes on the way back in).


def _stamp_to_jsonable(stamp: ProvenanceStamp) -> dict[str, object]:
    return {
        "calc_ts": stamp.calc_ts.isoformat(),
        "code_version": stamp.code_version,
        "config_hashes": dict(stamp.config_hashes),
        "source_records": [
            {"table": ref.table, "primary_key": list(ref.primary_key)}
            for ref in stamp.source_records
        ],
        "source_timestamps": [ts.isoformat() for ts in stamp.source_timestamps],
        "stamp_hash": stamp.stamp_hash,
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


def _fill_to_jsonable(fill: Fill) -> dict[str, object]:
    return {
        "fill_id": fill.fill_id,
        "booking_id": fill.booking_id,
        "source_basket_id": fill.source_basket_id,
        "trade_date": fill.trade_date.isoformat(),
        "underlying": fill.underlying,
        "contract_key": fill.contract_key,
        "signed_qty": str(fill.signed_qty),
        "price": fill.price,
        "fill_ts": fill.fill_ts.isoformat(),
        "mode": fill.mode,
        "broker_contract_id": fill.broker_contract_id,
        "provenance": _stamp_to_jsonable(fill.provenance),
    }


def _fill_from_jsonable(payload: Any) -> Fill:
    try:
        return Fill(
            fill_id=str(payload["fill_id"]),
            booking_id=str(payload["booking_id"]),
            source_basket_id=str(payload["source_basket_id"]),
            trade_date=date.fromisoformat(str(payload["trade_date"])),
            underlying=str(payload["underlying"]),
            contract_key=str(payload["contract_key"]),
            signed_qty=Decimal(str(payload["signed_qty"])),
            price=float(payload["price"]),
            fill_ts=datetime.fromisoformat(str(payload["fill_ts"])),
            provenance=_stamp_from_jsonable(payload["provenance"]),
            mode=str(payload["mode"]),
            broker_contract_id=(
                None
                if payload["broker_contract_id"] is None
                else str(payload["broker_contract_id"])
            ),
        )
    except (KeyError, ValueError, FillError) as exc:
        raise FillsLedgerError(
            f"malformed fill record on read: {exc}", field="record", value=payload
        ) from exc
