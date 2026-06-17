from __future__ import annotations

import json
import random
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import ProvenanceStamp, code_version, source_ref, stamp
from algotrading.execution.transmit.audit import (
    EVENT_DECISION,
    EVENT_GATE_EVALUATED,
    EVENT_TRANSMIT_ATTEMPT,
    InMemoryTransmitAuditLog,
    JsonlTransmitAuditLog,
    TransmitAudit,
    TransmitAuditError,
    replay,
)

TS = datetime(2026, 6, 12, 9, 0, tzinfo=UTC)
BINDING = "a" * 64


def _stamp(event: str) -> ProvenanceStamp:
    return stamp(
        calc_ts=TS,
        code_version=code_version("algotrading-execution"),
        config_hashes={"execution": "deadbeef"},
        source_records=(source_ref("transmit_decisions", BINDING, event),),
        source_timestamps=(TS,),
    )


def _record(event: str, sequence: int, *, event_id: str | None = None) -> TransmitAudit:
    return TransmitAudit(
        event_id=event_id if event_id is not None else f"{BINDING}-{sequence}",
        binding_hash=BINDING,
        event=event,
        sequence=sequence,
        detail=event,
        event_ts=TS,
        provenance=_stamp(event),
    )


_SEQUENCE = (
    (EVENT_GATE_EVALUATED, 0),
    (EVENT_DECISION, 1),
    (EVENT_TRANSMIT_ATTEMPT, 2),
)


def test_append_only_rejects_a_duplicate_id() -> None:
    log = InMemoryTransmitAuditLog()
    log.append(_record(EVENT_GATE_EVALUATED, 0, event_id="dup"))
    with pytest.raises(TransmitAuditError) as exc:
        log.append(_record(EVENT_DECISION, 1, event_id="dup"))
    assert exc.value.field == "event_id"


def test_jsonl_log_is_append_only_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "transmit_audit.jsonl"
    log = JsonlTransmitAuditLog(path)
    for event, seq in _SEQUENCE:
        log.append(_record(event, seq))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    reopened = JsonlTransmitAuditLog(path)
    assert [r.event for r in reopened.read()] == [EVENT_GATE_EVALUATED, EVENT_DECISION, EVENT_TRANSMIT_ATTEMPT]


def test_replay_is_reorder_stable() -> None:
    ordered = [_record(event, seq) for event, seq in _SEQUENCE]
    shuffled = list(ordered)
    random.Random(7).shuffle(shuffled)
    assert [r.sequence for r in replay(tuple(shuffled))] == [0, 1, 2]
    assert replay(tuple(shuffled)) == replay(tuple(ordered))


def test_malformed_record_on_read_raises_labeled_not_crashes(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps({"event_id": "x"}) + "\n", encoding="utf-8")
    with pytest.raises(TransmitAuditError) as exc:
        JsonlTransmitAuditLog(path)
    assert exc.value.field == "record"


def test_an_unknown_event_name_is_rejected() -> None:
    with pytest.raises(TransmitAuditError) as exc:
        _record("not_an_event", 0)
    assert exc.value.field == "event"


def test_a_naive_timestamp_is_rejected() -> None:
    with pytest.raises(TransmitAuditError) as exc:
        TransmitAudit(
            event_id="x",
            binding_hash=BINDING,
            event=EVENT_DECISION,
            sequence=0,
            detail="d",
            event_ts=datetime(2026, 6, 12, 9, 0),
            provenance=_stamp(EVENT_DECISION),
        )
    assert exc.value.field == "event_ts"


_SUBPROCESS_HASH = """
import json, sys
from datetime import UTC, datetime
from algotrading.core.provenance import code_version, source_ref, stamp
TS = datetime(2026, 6, 12, 9, 0, tzinfo=UTC)
s = stamp(
    calc_ts=TS,
    code_version="fixed-version",
    config_hashes={"execution": "deadbeef"},
    source_records=(source_ref("transmit_decisions", "a"*64, "decision"),),
    source_timestamps=(TS,),
)
print(s.stamp_hash)
"""


def test_stamp_hash_is_stable_across_separate_processes() -> None:
    runs = [
        subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_HASH],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        for _ in range(2)
    ]
    assert runs[0] == runs[1] != ""
