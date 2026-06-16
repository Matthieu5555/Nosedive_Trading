from __future__ import annotations

from dataclasses import dataclass
from datetime import date

OPTION_RIGHTS = ("C", "P")

_CANONICAL_FIELD_ORDER = (
    "underlying_symbol",
    "security_type",
    "exchange",
    "currency",
    "multiplier",
    "broker_contract_id",
    "expiry",
    "strike",
    "option_right",
)
_BROKER_CONTRACT_ID_SLOT = _CANONICAL_FIELD_ORDER.index("broker_contract_id")

EVENT_TIMESTAMP_FIELDS = ("exchange_ts", "receipt_ts", "canonical_ts")


@dataclass(frozen=True, slots=True)
class InstrumentKey:

    underlying_symbol: str
    security_type: str
    exchange: str
    currency: str
    multiplier: float
    broker_contract_id: str
    expiry: date | None = None
    strike: float | None = None
    option_right: str | None = None

    def is_option(self) -> bool:
        return self.expiry is not None

    def canonical(self) -> str:
        strike = "" if self.strike is None else format(self.strike, ".10g")
        expiry = "" if self.expiry is None else self.expiry.isoformat()
        right = self.option_right or ""
        return "|".join(
            (
                self.underlying_symbol,
                self.security_type,
                self.exchange,
                self.currency,
                format(self.multiplier, ".10g"),
                self.broker_contract_id,
                expiry,
                strike,
                right,
            )
        )


def broker_contract_id_from_canonical(canonical_key: str) -> str:
    fields = canonical_key.split("|")
    if len(fields) != len(_CANONICAL_FIELD_ORDER):
        raise ValueError(
            f"not a canonical instrument key: expected {len(_CANONICAL_FIELD_ORDER)} "
            f"pipe-joined fields, got {len(fields)} in {canonical_key!r}"
        )
    return fields[_BROKER_CONTRACT_ID_SLOT]
