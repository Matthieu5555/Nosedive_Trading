"""The fills ledger: append-only, auditable, replayable.

Both implementations (in-memory and durable JSONL) must share the invariants: append order
preserved, duplicate ``fill_id`` rejected, no mutate/delete verb, a tampered provenance stamp
refused at the door. The JSONL ledger adds durability — a file that only grows and replays.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from algotrading.core.provenance import ProvenanceStamp, ProvenanceValidationError
from algotrading.execution import (
    Fill,
    FillsLedger,
    FillsLedgerError,
    InMemoryFillsLedger,
    JsonlFillsLedger,
)


def _ledgers(tmp_path: Path) -> list[FillsLedger]:
    """One of each implementation, so every invariant is asserted on both."""
    return [InMemoryFillsLedger(), JsonlFillsLedger(tmp_path / "fills.jsonl")]


def test_append_preserves_order_and_filters_on_read(
    make_fill: Callable[..., Fill], tmp_path: Path
) -> None:
    for ledger in _ledgers(tmp_path):
        a = make_fill(fill_id="f-a", contract_key="SX5E|OPT|C|4400")
        b = make_fill(fill_id="f-b", contract_key="SX5E|OPT|P|4200", underlying="SX5E")
        c = make_fill(fill_id="f-c", underlying="SPX", contract_key="SPX|OPT|C|5000")
        ledger.append_many([a, b, c])
        assert [f.fill_id for f in ledger.read()] == ["f-a", "f-b", "f-c"]
        assert [f.fill_id for f in ledger.read(underlying="SX5E")] == ["f-a", "f-b"]
        assert [f.fill_id for f in ledger.read(underlying="SPX")] == ["f-c"]


def test_read_filters_by_trade_date(make_fill: Callable[..., Fill], tmp_path: Path) -> None:
    for ledger in _ledgers(tmp_path):
        mon = make_fill(fill_id="f-mon", trade_date=date(2026, 6, 12))
        tue = make_fill(fill_id="f-tue", trade_date=date(2026, 6, 15))
        ledger.append_many([mon, tue])
        assert [f.fill_id for f in ledger.read(trade_date=date(2026, 6, 15))] == ["f-tue"]


def test_a_duplicate_fill_id_is_rejected_append_only(
    make_fill: Callable[..., Fill], tmp_path: Path
) -> None:
    for ledger in _ledgers(tmp_path):
        ledger.append(make_fill(fill_id="dup"))
        with pytest.raises(FillsLedgerError) as exc:
            # Even a fill that differs in every other field cannot reuse an id — append-only
            # means no overwrite, ever.
            ledger.append(make_fill(fill_id="dup", signed_qty=Decimal("99"), price=1.0))
        assert exc.value.field == "fill_id"


def test_a_duplicate_inside_one_batch_is_caught(
    make_fill: Callable[..., Fill], tmp_path: Path
) -> None:
    for ledger in _ledgers(tmp_path):
        with pytest.raises(FillsLedgerError):
            ledger.append_many([make_fill(fill_id="x"), make_fill(fill_id="x")])


def test_a_tampered_provenance_stamp_is_refused_at_the_door(
    make_fill: Callable[..., Fill], tmp_path: Path
) -> None:
    fill = make_fill()
    # Mutate the stamp so its stored hash no longer matches its contents.
    bad_stamp = dataclasses.replace(fill.provenance, code_version="forged")
    tampered = dataclasses.replace(fill, provenance=bad_stamp)
    for ledger in _ledgers(tmp_path):
        # The door recomputes the stamp hash; a mutated stamp no longer matches.
        with pytest.raises(ProvenanceValidationError):
            ledger.append(tampered)


def test_the_ledger_exposes_no_mutation_or_delete_verb() -> None:
    # The append-only guarantee is structural: there is no update/delete/overwrite method.
    forbidden = {"update", "delete", "remove", "overwrite", "set", "pop", "clear", "mutate"}
    for impl in (InMemoryFillsLedger, JsonlFillsLedger):
        names = {n for n in dir(impl) if not n.startswith("_")}
        assert names & forbidden == set(), f"{impl.__name__} exposes a mutation verb"


def test_jsonl_ledger_is_durable_and_replays_with_a_faithful_stamp(
    make_fill: Callable[..., Fill], tmp_path: Path
) -> None:
    path = tmp_path / "fills.jsonl"
    original = make_fill(signed_qty=Decimal("-4"), broker_contract_id="conid-12")
    JsonlFillsLedger(path).append(original)

    # A fresh handle on the same file recovers the fill — durably, across "restarts".
    reopened = JsonlFillsLedger(path)
    (replayed,) = reopened.read()
    assert replayed.fill_id == original.fill_id
    assert replayed.signed_qty == Decimal("-4")
    assert replayed.broker_contract_id == "conid-12"
    # The stamp round-trips exactly — its content hash still validates after persistence.
    assert isinstance(replayed.provenance, ProvenanceStamp)
    assert replayed.provenance.stamp_hash == original.provenance.stamp_hash
    # And the duplicate guard survives the restart.
    with pytest.raises(FillsLedgerError):
        reopened.append(make_fill(fill_id=original.fill_id))


def test_jsonl_file_only_grows_one_line_per_fill(
    make_fill: Callable[..., Fill], tmp_path: Path
) -> None:
    path = tmp_path / "fills.jsonl"
    ledger = JsonlFillsLedger(path)
    ledger.append(make_fill(fill_id="f-1"))
    after_one = path.read_text(encoding="utf-8")
    ledger.append(make_fill(fill_id="f-2"))
    after_two = path.read_text(encoding="utf-8")
    # Append-only on disk: the first line is a byte-for-byte prefix of the file after the second.
    assert after_two.startswith(after_one)
    assert len(after_two.splitlines()) == 2
