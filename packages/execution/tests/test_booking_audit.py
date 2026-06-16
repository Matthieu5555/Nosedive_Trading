from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from algotrading.core.provenance import (
    ProvenanceStamp,
    ProvenanceValidationError,
    source_ref,
    stamp,
)
from algotrading.execution.booking import (
    BookingAudit,
    BookingAuditError,
    InMemoryBookingAuditLog,
    JsonlBookingAuditLog,
)

NOW = datetime(2026, 6, 12, 16, 0, tzinfo=UTC)


def _stamp(*refs: tuple[str, str, str]) -> ProvenanceStamp:
    records = tuple(source_ref(t, a, b) for t, a, b in refs)
    return stamp(
        calc_ts=NOW,
        code_version="algotrading-execution/test",
        config_hashes={"execution": "deadbeef"},
        source_records=records,
        source_timestamps=tuple(NOW for _ in records),
    )


def _commit(audit_id: str, fill_ids: tuple[str, ...], prov: ProvenanceStamp) -> BookingAudit:
    return BookingAudit(
        audit_id=audit_id,
        booking_id=audit_id,
        source_basket_id="bsk-1",
        trade_date=NOW.date(),
        underlying="SX5E",
        decision="commit",
        fill_ids=fill_ids,
        decision_ts=NOW,
        provenance=prov,
    )


def _block(audit_id: str, reason: str, prov: ProvenanceStamp) -> BookingAudit:
    return BookingAudit(
        audit_id=audit_id,
        booking_id=audit_id,
        source_basket_id="bsk-1",
        trade_date=NOW.date(),
        underlying="SX5E",
        decision="block",
        fill_ids=(),
        decision_ts=NOW,
        provenance=prov,
        block_reason=reason,
    )


@pytest.fixture(params=["memory", "jsonl"])
def audit_log(request: pytest.FixtureRequest, tmp_path: Path) -> object:
    if request.param == "memory":
        return InMemoryBookingAuditLog()
    return JsonlBookingAuditLog(tmp_path / "booking_audit.jsonl")


def test_a_record_is_appended_and_read_back_in_order(audit_log: object) -> None:
    log: InMemoryBookingAuditLog = audit_log  # type: ignore[assignment]
    log.append(_commit("a1", ("fill-0",), _stamp(("fills", "fill-0", "k"))))
    log.append(_block("a2", "wrong_password", _stamp(("order_tickets", "bsk-1", "a2"))))
    records = log.read()
    assert [r.audit_id for r in records] == ["a1", "a2"]
    assert [r.decision for r in records] == ["commit", "block"]


def test_a_duplicate_audit_id_is_rejected(audit_log: object) -> None:
    log: InMemoryBookingAuditLog = audit_log  # type: ignore[assignment]
    log.append(_commit("a1", ("fill-0",), _stamp(("fills", "fill-0", "k"))))
    with pytest.raises(BookingAuditError) as exc:
        log.append(_commit("a1", ("fill-1",), _stamp(("fills", "fill-1", "k"))))
    assert exc.value.field == "audit_id"
    assert log.read()[0].fill_ids == ("fill-0",)


def test_the_log_offers_no_mutate_or_delete_verb(audit_log: object) -> None:
    for forbidden in ("delete", "remove", "update", "pop", "clear", "mutate"):
        assert not hasattr(audit_log, forbidden)


def test_a_record_with_a_tampered_stamp_is_refused_at_the_door(audit_log: object) -> None:
    log: InMemoryBookingAuditLog = audit_log  # type: ignore[assignment]
    good = _stamp(("fills", "fill-0", "k"))
    forged = ProvenanceStamp(
        calc_ts=good.calc_ts,
        code_version=good.code_version,
        config_hashes=good.config_hashes,
        source_records=good.source_records,
        source_timestamps=good.source_timestamps,
        stamp_hash="0" * 64,
    )
    with pytest.raises(ProvenanceValidationError):
        log.append(_commit("a1", ("fill-0",), forged))
    assert log.read() == ()


def test_a_block_must_name_a_reason_and_a_commit_must_not() -> None:
    prov = _stamp(("fills", "fill-0", "k"))
    with pytest.raises(BookingAuditError) as block_exc:
        _block("a1", "", prov)
    assert block_exc.value.field == "block_reason"
    with pytest.raises(BookingAuditError) as commit_exc:
        BookingAudit(
            audit_id="a1", booking_id="a1", source_basket_id="bsk-1", trade_date=NOW.date(),
            underlying="SX5E", decision="commit", fill_ids=("fill-0",), decision_ts=NOW,
            provenance=prov, block_reason="should-not-be-here",
        )
    assert commit_exc.value.field == "block_reason"


def test_the_stamp_is_unchanged_when_source_fills_are_reordered() -> None:
    forward = _stamp(("fills", "fill-0", "k0"), ("fills", "fill-1", "k1"))
    reversed_ = _stamp(("fills", "fill-1", "k1"), ("fills", "fill-0", "k0"))
    assert forward.stamp_hash == reversed_.stamp_hash


def test_a_jsonl_replay_reconstructs_the_sequence_and_preserves_the_stamp(
    tmp_path: Path,
) -> None:
    path = tmp_path / "audit.jsonl"
    first = JsonlBookingAuditLog(path)
    a1 = _commit("a1", ("fill-0",), _stamp(("fills", "fill-0", "k")))
    a2 = _block("a2", "unresolvable_leg", _stamp(("order_tickets", "bsk-1", "a2")))
    first.append(a1)
    first.append(a2)
    replayed = JsonlBookingAuditLog(path)
    records = replayed.read()
    assert [r.audit_id for r in records] == ["a1", "a2"]
    assert records[0].provenance.stamp_hash == a1.provenance.stamp_hash
    assert records[1].provenance.stamp_hash == a2.provenance.stamp_hash
    with pytest.raises(BookingAuditError):
        replayed.append(_commit("a1", ("fill-9",), _stamp(("fills", "fill-9", "k"))))


def test_the_audit_stamp_hash_is_stable_across_processes() -> None:
    expected = _stamp(("fills", "fill-0", "k0"), ("fills", "fill-1", "k1")).stamp_hash
    code = (
        "from datetime import UTC, datetime;"
        "from algotrading.core.provenance import source_ref, stamp;"
        "ts=datetime(2026,6,12,16,0,tzinfo=UTC);"
        "s=stamp(calc_ts=ts,code_version='algotrading-execution/test',"
        "config_hashes={'execution':'deadbeef'},"
        "source_records=(source_ref('fills','fill-0','k0'),source_ref('fills','fill-1','k1')),"
        "source_timestamps=(ts,ts));"
        "print(s.stamp_hash)"
    )
    for seed in ("0", "1", "424242"):
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "PYTHONHASHSEED": seed},
        )
        assert out.stdout.strip() == expected
