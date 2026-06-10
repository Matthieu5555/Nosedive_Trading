"""Bridge the broker-raw sample wire-format ↔ the canonical contracts raw event (ADR 0039).

The committed sample fixtures under ``packages/infra-{ibkr,saxo}/samples/`` are the broker-raw
EAV schema (:class:`storage.events.RawMarketEvent`: ``collector_session_id`` / ``field_value:
Decimal`` / colon-delimited ``OPT:`` keys / ``provider`` / ``contract_id_broker``). The canonical
raw layer the ``ParquetStore`` persists is the contracts schema
(:class:`contracts.RawMarketEvent`: ``session_id`` / ``value: float`` / pipe-delimited keys /
``canonical_ts`` / ``trade_date``). ADR 0019 makes the contracts schema the one raw model; this
module is the **single** place the two schemas convert — replacing the conversion that used to be
hand-copied inline in the IBKR pipeline notebook.

It lives in ``universe`` (not ``storage``) because ``universe`` already imports ``storage``
(``membership.py`` reads ``ParquetStore``) and owns the colon-key vocabulary; a ``storage`` module
importing ``universe.parse_instrument_key`` would create a storage↔universe import cycle (ADR 0039
placement note).

Field mapping (broker-raw → contracts), per the ADR rulings:

- ``collector_session_id`` → ``session_id`` (rename).
- ``event_id`` (broker-native) → ``event_id`` re-derived ``content_event_id(pipe, field, seq)``.
- ``instrument_key`` colon → pipe (``parse_instrument_key`` → ``InstrumentKey.canonical``).
- ``field_value`` (Decimal) → ``value`` (float) — see the OQ-B precision note below.
- ``receipt_ts`` → ``receipt_ts`` (identity).
- ``exchange_ts`` (optional) → ``exchange_ts`` and ``canonical_ts`` = ``exchange_ts or receipt_ts``.
- ``underlying`` → ``underlying`` (identity).
- ``provider`` → dropped (re-supplied as an argument on export, OQ-A).
- ``contract_id_broker`` → carried in the pipe key's ``broker_contract_id`` slot.
- ``trade_date`` ← caller-supplied (not in the broker-raw event).

The reverse inverts each row: ``provider`` is an argument (OQ-A), ``Decimal`` is
``Decimal(str(value))`` — exact to the stored float precision, not the broker's original Decimal,
already lost at capture (OQ-B) — and ``contract_id_broker`` is recovered from the pipe key slot.
"""

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
from algotrading.infra.storage.events import RawMarketEvent as BrokerRawEvent

from .contracts import (
    OptionContract,
    Right,
    Underlying,
    parse_instrument_key,
)
from .contracts import (
    instrument_key as colon_instrument_key,
)

# A categorical/absent broker value (``str``/``None``) has no place in the contracts schema
# (``value: float``); such events are skipped on the broker-raw → contracts direction.
_NON_NUMERIC = (str, type(None))


def _colon_to_pipe(colon_key: str, broker_contract_id: str) -> str:
    """Relabel a broker-raw colon key into the canonical contracts pipe key."""
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
    """Relabel a canonical contracts pipe key back into a broker-raw colon key."""
    fields = pipe_key.split("|")
    if len(fields) != 9:
        raise ValueError(f"not a canonical instrument key: {pipe_key!r}")
    symbol, security_type, exchange, currency, mult, _broker, expiry, strike, right = fields
    domain: Underlying | OptionContract
    if not expiry:  # underlying — the option-only slots are empty
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
    """Convert broker-raw sample events into canonical contracts raw events.

    Numeric observations only: a ``None``/``str`` ``field_value`` has no place in the contracts
    ``value: float`` and is skipped. ``event_id`` is re-derived as
    ``content_event_id(pipe_key, field, seq)`` with ``seq`` the per-``(pipe_key, field_name)``
    ordinal in input order — the same id a live or replayed capture would produce.
    """
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
    """Convert canonical contracts raw events into broker-raw sample events.

    ``provider`` is re-supplied (the contracts schema dropped it, OQ-A); ``contract_id_broker`` is
    recovered from the pipe key's broker slot (empty → ``None``); ``value: float`` becomes
    ``Decimal(str(value))`` — exact to the stored precision, not the broker's original Decimal,
    already lost at the capture boundary (OQ-B).
    """
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
