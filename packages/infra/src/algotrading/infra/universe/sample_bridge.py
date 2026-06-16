from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from algotrading.infra.contracts import RawMarketEvent as ContractsRawEvent
from algotrading.infra.contracts.broker import content_event_id
from algotrading.infra.contracts.instrument_key import (
    InstrumentKey,
    broker_contract_id_from_canonical,
)
from algotrading.infra.storage.events import CollectorEvent as BrokerRawEvent

from .contracts import (
    OptionContract,
    Right,
    Underlying,
    parse_instrument_key,
)
from .contracts import (
    instrument_key as colon_instrument_key,
)

_NON_NUMERIC = (str, type(None))


def _colon_to_pipe(colon_key: str, broker_contract_id: str) -> str:
    domain = parse_instrument_key(colon_key)
    if isinstance(domain, Underlying):
        return InstrumentKey(
            underlying_symbol=domain.symbol,
            security_type=domain.security_type,
            exchange=domain.exchange,
            currency=domain.currency,
            multiplier=1.0,
            broker_contract_id=broker_contract_id,
        ).canonical()
    return InstrumentKey(
        underlying_symbol=domain.symbol,
        security_type=domain.security_type,
        exchange=domain.exchange,
        currency=domain.currency,
        multiplier=float(domain.multiplier),
        broker_contract_id=broker_contract_id,
        expiry=domain.expiry,
        strike=float(domain.strike),
        option_right=domain.right.value,
    ).canonical()


def _pipe_to_colon(pipe_key: str) -> str:
    fields = pipe_key.split("|")
    if len(fields) != 9:
        raise ValueError(f"not a canonical instrument key: {pipe_key!r}")
    symbol, security_type, exchange, currency, mult, _broker, expiry, strike, right = fields
    domain: Underlying | OptionContract
    if not expiry:
        domain = Underlying(
            symbol=symbol,
            security_type=security_type,
            exchange=exchange,
            currency=currency,
        )
    else:
        domain = OptionContract(
            symbol=symbol,
            security_type=security_type,
            expiry=date.fromisoformat(expiry),
            strike=Decimal(strike),
            right=Right(right),
            multiplier=int(float(mult)),
            exchange=exchange,
            currency=currency,
        )
    return colon_instrument_key(domain)


def events_to_contracts(
    broker_events: Sequence[BrokerRawEvent], *, trade_date: date
) -> list[ContractsRawEvent]:
    out: list[ContractsRawEvent] = []
    sequence: dict[tuple[str, str], int] = {}
    for event in broker_events:
        if isinstance(event.field_value, _NON_NUMERIC):
            continue
        broker_id = event.contract_id_broker or event.instrument_key
        pipe_key = _colon_to_pipe(event.instrument_key, broker_id)
        seq_key = (pipe_key, event.field_name)
        seq = sequence.get(seq_key, 0)
        sequence[seq_key] = seq + 1
        canonical_ts = event.exchange_ts or event.receipt_ts
        out.append(
            ContractsRawEvent(
                session_id=event.collector_session_id,
                event_id=content_event_id(pipe_key, event.field_name, seq),
                instrument_key=pipe_key,
                exchange_ts=canonical_ts,
                receipt_ts=event.receipt_ts,
                canonical_ts=canonical_ts,
                field_name=event.field_name,
                value=float(event.field_value),
                trade_date=trade_date,
                underlying=event.underlying,
            )
        )
    return out


def contracts_to_events(
    contract_events: Sequence[ContractsRawEvent], *, provider: str
) -> list[BrokerRawEvent]:
    out: list[BrokerRawEvent] = []
    for event in contract_events:
        broker_id = broker_contract_id_from_canonical(event.instrument_key)
        out.append(
            BrokerRawEvent(
                collector_session_id=event.session_id,
                event_id=event.event_id,
                receipt_ts=event.receipt_ts,
                instrument_key=_pipe_to_colon(event.instrument_key),
                field_name=event.field_name,
                field_value=Decimal(str(event.value)),
                underlying=event.underlying,
                provider=provider,
                exchange_ts=event.exchange_ts,
                contract_id_broker=broker_id or None,
            )
        )
    return out
