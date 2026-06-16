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

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@runtime_checkable
class FillsLedger(Protocol):

    def append(self, fill: Fill) -> None: ...

    def append_many(self, fills: Iterable[Fill]) -> None: ...

    def read(
        self, *, trade_date: date | None = None, underlying: str | None = None
    ) -> tuple[Fill, ...]: ...


def _validated(fill: Fill, *, seen: frozenset[str]) -> None:
    if not isinstance(fill, Fill):
        raise FillsLedgerError("must be a Fill", field="fill", value=fill)
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

    def __init__(self) -> None:
        self._fills: list[Fill] = []
        self._ids: set[str] = set()

    def append(self, fill: Fill) -> None:
        _validated(fill, seen=frozenset(self._ids))
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


class JsonlFillsLedger:

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
