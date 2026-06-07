"""WS 1A — membership *source* readers: raw vendor CSV → typed dated changes.

Expected values are derived independently of the code under test (TESTING.md): the sample CSV
below is hand-authored and every expected ``effective_add_date`` / basket is computed by hand
from it, never by calling the parser/resolver and asserting it equals itself. The load-bearing
case is the dated one — a name added in 2010 must be **out** of a 2005 as-of basket and **in** a
2022 one — which is what proves the add date is honored end to end (parse → ingest → resolve).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import (
    ingest_membership_changes,
    members,
    parse_constituents_csv,
)

# A datasets/s-and-p-500-companies-shaped snapshot: a "Date added" column (real add dates), one
# row with a blank date (must fall back to the inception default), mixed case + a duplicate.
SAMPLE_CSV = """Symbol,Security,Date added
AAA,Alpha Corp,2010-01-04
BBB,Beta Inc,
ccc,Gamma Ltd,2021-09-20
AAA,Alpha Corp dup,2099-12-31
"""

KNOWLEDGE = date(2026, 6, 7)
VENDOR = "github:datasets/s-and-p-500-companies"
INCEPTION = date(1957, 3, 4)


def _parse() -> list:
    return parse_constituents_csv(
        SAMPLE_CSV,
        index="SPX",
        vendor=VENDOR,
        knowledge_date=KNOWLEDGE,
        default_add_date=INCEPTION,
        symbol_field="Symbol",
        add_date_field="Date added",
    )


def test_parse_maps_symbols_dates_and_metadata() -> None:
    changes = {c.constituent: c for c in _parse()}
    # Three distinct names (the duplicate AAA row is dropped, first wins); symbols upper-cased.
    assert set(changes) == {"AAA", "BBB", "CCC"}
    assert changes["AAA"].effective_add_date == date(2010, 1, 4)
    assert changes["CCC"].effective_add_date == date(2021, 9, 20)
    for change in changes.values():
        assert change.index == "SPX"
        assert change.vendor == VENDOR
        assert change.knowledge_date == KNOWLEDGE
        assert change.effective_remove_date is None  # a current snapshot states no removals
        assert change.weight is None  # labeled-unavailable, never zeroed (OQ-1/OQ-3)


def test_blank_add_date_falls_back_to_default() -> None:
    changes = {c.constituent: c for c in _parse()}
    assert changes["BBB"].effective_add_date == INCEPTION


def test_weight_column_is_read_when_present() -> None:
    csv_text = "Symbol,Weight\nAAA,3.5\nBBB,\n"
    changes = {
        c.constituent: c
        for c in parse_constituents_csv(
            csv_text,
            index="SPX",
            vendor=VENDOR,
            knowledge_date=KNOWLEDGE,
            default_add_date=INCEPTION,
            weight_field="Weight",
        )
    }
    assert changes["AAA"].weight == 3.5
    assert changes["BBB"].weight is None  # blank → unavailable, not zero


def test_parsed_changes_ingest_and_resolve_point_in_time(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ingest_membership_changes(store, _parse())

    def basket(on: date) -> set[str]:
        return {m.constituent for m in members(store, "SPX", on)}

    # Hand-computed from the sample: BBB in since inception (1957); AAA since 2010-01-04;
    # CCC since 2021-09-20. None ever removed.
    assert basket(date(2005, 1, 1)) == {"BBB"}  # before AAA and CCC were added
    assert basket(date(2015, 6, 1)) == {"AAA", "BBB"}  # CCC not yet in
    assert basket(date(2022, 1, 1)) == {"AAA", "BBB", "CCC"}  # all in


def test_reingest_is_idempotent(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ingest_membership_changes(store, _parse())
    ingest_membership_changes(store, _parse())  # same bitemporal key + payload → no-op, no raise
    assert {m.constituent for m in members(store, "SPX", date(2022, 1, 1))} == {"AAA", "BBB", "CCC"}
