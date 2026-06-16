from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

import structlog
from algotrading.infra.contracts import ConidEntry, DiscoveryCacheRow
from algotrading.infra.storage.adapter import ParquetStore

from ..connectivity.cp_rest_transport import SupportsRestGet

__all__ = [
    "DEFAULT_MAX_AGE_DAYS",
    "TRSRV_SECDEF_BATCH",
    "CachedChain",
    "ConidEntry",
    "DiscoveryCache",
    "DiscoveryCacheRow",
    "revalidate_conids",
]

_LOGGER = structlog.get_logger("ibkr.discovery_cache")

DEFAULT_MAX_AGE_DAYS = 5

TRSRV_SECDEF_BATCH = 200

_TABLE = "discovery_conid_cache"


@dataclass(frozen=True, slots=True)
class CachedChain:

    underlying: str
    as_of_date: date
    exchange: str
    multiplier: str
    months: tuple[str, ...]
    expirations: tuple[str, ...]
    strikes: tuple[float, ...]
    conid_by_contract: Mapping[str, str] = field(default_factory=dict)

    @property
    def conids(self) -> tuple[int, ...]:
        seen: set[int] = set()
        for raw in self.conid_by_contract.values():
            try:
                seen.add(int(raw))
            except (TypeError, ValueError):
                continue
        return tuple(sorted(seen))


def _contract_token(expiry: str, strike: float, right: str) -> str:
    return f"{expiry}|{strike:.10g}|{right}"


class DiscoveryCache:

    def __init__(self, store: ParquetStore, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> None:
        if max_age_days < 0:
            raise ValueError(f"max_age_days must be >= 0, got {max_age_days}")
        self._store = store
        self._max_age_days = max_age_days

    @property
    def max_age_days(self) -> int:
        return self._max_age_days

    def store_chain(
        self,
        *,
        underlying: str,
        as_of: date,
        exchange: str,
        multiplier: str,
        months: Sequence[str],
        expirations: Sequence[str],
        strikes: Sequence[float],
        conid_by_contract: Mapping[str, str],
        entry_expiry_by_token: Mapping[str, str] | None = None,
        entry_month_by_token: Mapping[str, str] | None = None,
    ) -> DiscoveryCacheRow:
        entry_expiry = dict(entry_expiry_by_token or {})
        entry_month = dict(entry_month_by_token or {})
        entries: list[ConidEntry] = []
        for token, conid in conid_by_contract.items():
            expiry, strike, right = _parse_token(token, entry_expiry.get(token))
            entries.append(
                ConidEntry(
                    month=entry_month.get(token, ""),
                    expiry=expiry,
                    strike=strike,
                    right=right,
                    conid=str(conid),
                )
            )
        entries.sort(key=lambda e: (e.expiry, e.strike, e.right))
        row = DiscoveryCacheRow(
            underlying=underlying,
            as_of_date=as_of,
            exchange=exchange,
            multiplier=multiplier,
            months=tuple(months),
            expirations=tuple(expirations),
            strikes=tuple(strikes),
            entries=tuple(entries),
        )
        self._store.write(_TABLE, [row])
        _LOGGER.info(
            "ibkr.discovery_cache.stored",
            underlying=underlying,
            as_of=as_of.isoformat(),
            entries=len(entries),
            expirations=len(row.expirations),
        )
        return row

    def fresh_for(self, *, as_of_date: date, capture_date: date) -> bool:
        age = (capture_date - as_of_date).days
        return 0 <= age <= self._max_age_days

    def load(self, *, underlying: str, capture_date: date) -> CachedChain | None:
        rows = [
            row
            for row in self._store.read(_TABLE, underlying=underlying)
            if isinstance(row, DiscoveryCacheRow)
            and self.fresh_for(as_of_date=row.as_of_date, capture_date=capture_date)
        ]
        if not rows:
            return None
        row = max(rows, key=lambda r: r.as_of_date)
        conid_by_contract = {
            _contract_token(entry.expiry, entry.strike, entry.right): entry.conid
            for entry in row.entries
        }
        _LOGGER.info(
            "ibkr.discovery_cache.hit",
            underlying=underlying,
            as_of=row.as_of_date.isoformat(),
            capture_date=capture_date.isoformat(),
            entries=len(conid_by_contract),
        )
        return CachedChain(
            underlying=row.underlying,
            as_of_date=row.as_of_date,
            exchange=row.exchange,
            multiplier=row.multiplier,
            months=row.months,
            expirations=row.expirations,
            strikes=row.strikes,
            conid_by_contract=conid_by_contract,
        )


def _parse_token(token: str, expiry_hint: str | None) -> tuple[str, float, str]:
    parts = token.split("|")
    if len(parts) != 3:
        raise ValueError(f"malformed discovery token {token!r} (expected 'expiry|strike|right')")
    expiry, strike_text, right = parts
    return (expiry_hint or expiry, float(strike_text), right)


def revalidate_conids(
    transport: SupportsRestGet,
    conids: Sequence[int],
    *,
    batch_size: int = TRSRV_SECDEF_BATCH,
) -> frozenset[int]:
    ordered = sorted({int(c) for c in conids})
    if not ordered:
        return frozenset()
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    valid: set[int] = set()
    requested = set(ordered)
    for start in range(0, len(ordered), batch_size):
        batch = ordered[start : start + batch_size]
        payload = transport.get(
            "/trsrv/secdef",
            params={"conids": ",".join(str(c) for c in batch)},
        )
        for conid in _parse_secdef_conids(payload):
            if conid in requested:
                valid.add(conid)
    return frozenset(valid)


def _parse_secdef_conids(payload: object) -> set[int]:
    rows = payload.get("secdef") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return set()
    conids: set[int] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        raw = row.get("conid")
        if raw is None:
            continue
        try:
            conids.add(int(raw))
        except (TypeError, ValueError):
            continue
    return conids
