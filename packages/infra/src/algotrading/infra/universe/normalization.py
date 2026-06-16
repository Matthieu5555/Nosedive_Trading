from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime

from algotrading.infra.contracts import InstrumentKey

from .errors import UnresolvedContractError

_RIGHT_ALIASES = {"C": "C", "CALL": "C", "P": "P", "PUT": "P"}

_EXPIRY_FORMATS = ("%Y%m%d", "%Y-%m-%d")


def _coerce_number(raw: object) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw)
        except ValueError:
            return None
    else:
        return None
    return value if math.isfinite(value) else None


def _require_text(payload: Mapping[str, object], field: str) -> str:
    raw = payload.get(field)
    if isinstance(raw, str) and raw.strip():
        return raw
    raise UnresolvedContractError(payload, field, f"must be a non-empty string, got {raw!r}")


def _require_broker_contract_id(payload: Mapping[str, object]) -> str:
    raw = payload.get("conId")
    if isinstance(raw, bool):
        raise UnresolvedContractError(payload, "conId", f"must be an id, got {raw!r}")
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, str) and raw.strip():
        return raw
    raise UnresolvedContractError(payload, "conId", f"must be a non-empty id, got {raw!r}")


def _require_currency(payload: Mapping[str, object]) -> str:
    raw = payload.get("currency")
    if isinstance(raw, str) and raw.strip():
        return raw
    raise UnresolvedContractError(
        payload, "currency", f"currency is required and must not be empty, got {raw!r}"
    )


def _require_multiplier(payload: Mapping[str, object]) -> float:
    raw = payload.get("multiplier")
    value = _coerce_number(raw)
    if value is None or value <= 0.0:
        raise UnresolvedContractError(
            payload, "multiplier", f"multiplier is required and must be positive, got {raw!r}"
        )
    return value


def _require_strike(payload: Mapping[str, object]) -> float:
    raw = payload.get("strike")
    value = _coerce_number(raw)
    if value is None or value <= 0.0:
        raise UnresolvedContractError(
            payload, "strike", f"strike must be a positive number, got {raw!r}"
        )
    return value


def normalize_expiry(payload: Mapping[str, object], raw: object) -> date:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        for fmt in _EXPIRY_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    raise UnresolvedContractError(
        payload, "expiry", f"unparseable expiry {raw!r}; expected YYYYMMDD or YYYY-MM-DD"
    )


def normalize_right(payload: Mapping[str, object], raw: object) -> str:
    if isinstance(raw, str):
        canonical = _RIGHT_ALIASES.get(raw.strip().upper())
        if canonical is not None:
            return canonical
    raise UnresolvedContractError(
        payload, "right", f"option right must be one of C/P/CALL/PUT, got {raw!r}"
    )


def resolve_contract_row(payload: Mapping[str, object]) -> InstrumentKey:
    symbol = _require_text(payload, "symbol")
    security_type = _require_text(payload, "secType")
    exchange = _require_text(payload, "exchange")
    currency = _require_currency(payload)
    multiplier = _require_multiplier(payload)
    broker_contract_id = _require_broker_contract_id(payload)

    expiry: date | None = None
    strike: float | None = None
    option_right: str | None = None
    if security_type == "OPT":
        expiry = normalize_expiry(payload, payload.get("expiry"))
        strike = _require_strike(payload)
        option_right = normalize_right(payload, payload.get("right"))

    return InstrumentKey(
        underlying_symbol=symbol,
        security_type=security_type,
        exchange=exchange,
        currency=currency,
        multiplier=multiplier,
        broker_contract_id=broker_contract_id,
        expiry=expiry,
        strike=strike,
        option_right=option_right,
    )
