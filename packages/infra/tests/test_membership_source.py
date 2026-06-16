from __future__ import annotations

from datetime import date
from pathlib import Path

from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import (
    CsvFileSource,
    ingest_membership_changes,
    members,
    parse_constituents_csv,
)

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
    assert set(changes) == {"AAA", "BBB", "CCC"}
    assert changes["AAA"].effective_add_date == date(2010, 1, 4)
    assert changes["CCC"].effective_add_date == date(2021, 9, 20)
    for change in changes.values():
        assert change.index == "SPX"
        assert change.vendor == VENDOR
        assert change.knowledge_date == KNOWLEDGE
        assert change.effective_remove_date is None
        assert change.weight is None


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
    assert changes["BBB"].weight is None


def test_parsed_changes_ingest_and_resolve_point_in_time(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ingest_membership_changes(store, _parse())

    def basket(on: date) -> set[str]:
        return {m.constituent for m in members(store, "SPX", on)}

    assert basket(date(2005, 1, 1)) == {"BBB"}
    assert basket(date(2015, 6, 1)) == {"AAA", "BBB"}
    assert basket(date(2022, 1, 1)) == {"AAA", "BBB", "CCC"}


def test_reingest_is_idempotent(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    ingest_membership_changes(store, _parse())
    ingest_membership_changes(store, _parse())
    assert {m.constituent for m in members(store, "SPX", date(2022, 1, 1))} == {"AAA", "BBB", "CCC"}


WEIGHTED_CSV = """Symbol,Weight
AAA,7.5
BBB,2.25
CCC,
"""


def test_csv_file_source_carries_weights(tmp_path: Path) -> None:
    path = tmp_path / "spx_weights.csv"
    path.write_text(WEIGHTED_CSV, encoding="utf-8")
    source = CsvFileSource(
        path=path,
        vendor="seed:test-snapshot-2026-06-10",
        default_add_date=date(2000, 1, 1),
    )
    changes = {c.constituent: c for c in source.fetch("SX5E", KNOWLEDGE)}

    assert set(changes) == {"AAA", "BBB", "CCC"}
    assert changes["AAA"].weight == 7.5
    assert changes["BBB"].weight == 2.25
    assert changes["CCC"].weight is None
    for change in changes.values():
        assert change.index == "SX5E"
        assert change.vendor == "seed:test-snapshot-2026-06-10"
        assert change.effective_add_date == date(2000, 1, 1)


def test_csv_file_source_weights_survive_ingest_and_resolve(tmp_path: Path) -> None:
    path = tmp_path / "spx_weights.csv"
    path.write_text(WEIGHTED_CSV, encoding="utf-8")
    store = ParquetStore(tmp_path / "store")
    source = CsvFileSource(path=path, vendor="seed:test", default_add_date=date(2000, 1, 1))
    ingest_membership_changes(store, source.fetch("SX5E", KNOWLEDGE))

    weight_by_symbol = {m.constituent: m.weight for m in members(store, "SX5E", date(2026, 6, 10))}
    assert weight_by_symbol == {"AAA": 7.5, "BBB": 2.25, "CCC": None}
