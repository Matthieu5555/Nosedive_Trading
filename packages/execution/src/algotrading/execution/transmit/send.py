from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from algotrading.core.provenance import ProvenanceStamp, code_version, source_ref, stamp

from .audit import (
    EVENT_DECISION,
    EVENT_GATE_EVALUATED,
    EVENT_TRANSMIT_ATTEMPT,
    TransmitAudit,
    TransmitAuditLog,
)
from .decision import SignoffVerifier, TransmissionDecision, decide_transmission
from .gate import GateLoad, load_transmit_gate_from_environment
from .signing import SignedTicket, signoff_token_valid_from_environment
from .sink import PaperSink, SinkOutcome, TransmitSink

_DISTRIBUTION = "algotrading-execution"

_TABLE = "transmit_decisions"


@dataclass(frozen=True, slots=True)
class TransmitResult:

    decision: TransmissionDecision
    outcome: SinkOutcome
    audit: tuple[TransmitAudit, ...]


def _event_stamp(
    *, now: datetime, config_hashes: Mapping[str, str], binding_hash: str, event: str
) -> ProvenanceStamp:
    return stamp(
        calc_ts=now,
        code_version=code_version(_DISTRIBUTION),
        config_hashes=config_hashes,
        source_records=(source_ref(_TABLE, binding_hash, event),),
        source_timestamps=(now,),
    )


def _record(
    audit_log: TransmitAuditLog,
    *,
    binding_hash: str,
    event: str,
    sequence: int,
    detail: str,
    now: datetime,
    config_hashes: Mapping[str, str],
    mint_event_id: Callable[[int], str],
) -> TransmitAudit:
    record = TransmitAudit(
        event_id=mint_event_id(sequence),
        binding_hash=binding_hash,
        event=event,
        sequence=sequence,
        detail=detail,
        event_ts=now,
        provenance=_event_stamp(
            now=now, config_hashes=config_hashes, binding_hash=binding_hash, event=event
        ),
    )
    audit_log.append(record)
    return record


def transmit(
    signed: SignedTicket,
    *,
    audit_log: TransmitAuditLog,
    now: datetime,
    config_hashes: Mapping[str, str],
    mint_event_id: Callable[[int], str],
    sink: TransmitSink | None = None,
    gate: GateLoad | None = None,
    verify_signoff: SignoffVerifier = signoff_token_valid_from_environment,
) -> TransmitResult:
    resolved_gate = gate if gate is not None else load_transmit_gate_from_environment()
    resolved_sink: TransmitSink = sink if sink is not None else PaperSink()
    binding_hash = signed.binding_hash

    records: list[TransmitAudit] = []
    records.append(
        _record(
            audit_log,
            binding_hash=binding_hash,
            event=EVENT_GATE_EVALUATED,
            sequence=0,
            detail=f"gate={resolved_gate!r}",
            now=now,
            config_hashes=config_hashes,
            mint_event_id=mint_event_id,
        )
    )

    decision = decide_transmission(
        signed, resolved_gate, now, verify_signoff=verify_signoff
    )
    records.append(
        _record(
            audit_log,
            binding_hash=binding_hash,
            event=EVENT_DECISION,
            sequence=1,
            detail=decision.value,
            now=now,
            config_hashes=config_hashes,
            mint_event_id=mint_event_id,
        )
    )

    outcome = resolved_sink.handle(signed, decision, now)
    records.append(
        _record(
            audit_log,
            binding_hash=binding_hash,
            event=EVENT_TRANSMIT_ATTEMPT,
            sequence=2,
            detail=outcome.detail,
            now=now,
            config_hashes=config_hashes,
            mint_event_id=mint_event_id,
        )
    )

    return TransmitResult(decision=decision, outcome=outcome, audit=tuple(records))
