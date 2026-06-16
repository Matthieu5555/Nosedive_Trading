from __future__ import annotations

import random
from datetime import date
from pathlib import Path

import pytest
from algotrading.infra.contracts import IndexConstituent, spec_for_table, validate_record
from algotrading.infra.contracts.errors import ContractValidationError
from algotrading.infra.storage import AppendOnlyViolation, ParquetStore
from algotrading.infra.universe import (
    BasketMember,
    MembershipChange,
    MembershipError,
    basket_weight_sum,
    ingest_membership_changes,
    members,
)

VENDOR = "Siblis"

KNOWN = date(2010, 1, 1)
SX5E_CHANGES = (
    MembershipChange("SX5E", "ASML", date(2010, 1, 1), None, KNOWN, VENDOR, 0.10),
    MembershipChange("SX5E", "ADYEN", date(2020, 9, 21), None, KNOWN, VENDOR, 0.05),
    MembershipChange("SX5E", "ENGI", date(2010, 1, 1), date(2021, 9, 20), KNOWN, VENDOR, 0.03),
)


def _store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(tmp_path)


def _names(store: ParquetStore, on: date, **kw: object) -> tuple[str, ...]:
    return tuple(m.constituent for m in members(store, "SX5E", on, **kw))


def test_basket_before_addition_excludes_the_added_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    assert _names(store, date(2019, 6, 30)) == ("ASML", "ENGI")


def test_basket_after_addition_before_removal_includes_both(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    assert _names(store, date(2021, 1, 15)) == ("ADYEN", "ASML", "ENGI")


def test_basket_after_removal_excludes_the_removed_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    assert _names(store, date(2022, 3, 1)) == ("ADYEN", "ASML")


def test_added_name_is_in_on_its_exact_add_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    assert "ADYEN" in _names(store, date(2020, 9, 21))
    assert _names(store, date(2020, 9, 21)) == ("ADYEN", "ASML", "ENGI")


def test_added_name_is_out_the_day_before_its_add_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    assert "ADYEN" not in _names(store, date(2020, 9, 20))


def test_removed_name_is_out_on_its_exact_remove_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    assert "ENGI" not in _names(store, date(2021, 9, 20))


def test_removed_name_is_in_the_day_before_its_remove_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    assert "ENGI" in _names(store, date(2021, 9, 19))


def test_past_basket_is_not_the_current_basket(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    current = _names(store, date(2024, 1, 2))
    past = _names(store, date(2019, 6, 30))
    assert current == ("ADYEN", "ASML")
    assert past == ("ASML", "ENGI")
    assert past != current
    assert "ENGI" in past and "ENGI" not in current
    assert "ADYEN" not in past and "ADYEN" in current


def test_weight_resolves_per_date_when_it_changes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    changes = (
        MembershipChange("SX5E", "X", date(2010, 1, 1), date(2021, 1, 1), KNOWN, VENDOR, 0.50),
        MembershipChange("SX5E", "X", date(2021, 1, 1), None, KNOWN, VENDOR, 0.60),
    )
    ingest_membership_changes(store, changes)
    [m_2015] = members(store, "SX5E", date(2015, 6, 1))
    [m_2022] = members(store, "SX5E", date(2022, 6, 1))
    assert m_2015.weight == pytest.approx(0.50, abs=1e-9)
    assert m_2022.weight == pytest.approx(0.60, abs=1e-9)


def test_basket_weights_sum_within_tolerance_when_source_complete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    changes = (
        MembershipChange("SX5E", "P", date(2010, 1, 1), None, KNOWN, VENDOR, 0.40),
        MembershipChange("SX5E", "Q", date(2010, 1, 1), None, KNOWN, VENDOR, 0.35),
        MembershipChange("SX5E", "R", date(2010, 1, 1), None, KNOWN, VENDOR, 0.25),
    )
    ingest_membership_changes(store, changes)
    basket = members(store, "SX5E", date(2020, 1, 1))
    total = basket_weight_sum(basket)
    assert total is not None
    assert total == pytest.approx(1.0, abs=1e-9)


def test_corrupt_complete_weight_snapshot_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bad = (
        MembershipChange("SX5E", "P", date(2010, 1, 1), None, KNOWN, VENDOR, 0.25),
        MembershipChange("SX5E", "Q", date(2010, 1, 1), None, KNOWN, VENDOR, 0.25),
    )
    with pytest.raises(MembershipError) as exc:
        ingest_membership_changes(store, bad, complete_snapshot=True)
    assert exc.value.field == "weight"
    assert store.read("index_constituents") == []


def test_complete_snapshot_with_a_missing_weight_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    contradictory = (
        MembershipChange("SX5E", "P", date(2010, 1, 1), None, KNOWN, VENDOR, 0.6),
        MembershipChange("SX5E", "Q", date(2010, 1, 1), None, KNOWN, VENDOR, None),
    )
    with pytest.raises(MembershipError) as exc:
        ingest_membership_changes(store, contradictory, complete_snapshot=True)
    assert exc.value.field == "weight"
    assert store.read("index_constituents") == []


def test_complete_snapshot_summing_to_one_is_accepted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    good = (
        MembershipChange("SX5E", "P", date(2010, 1, 1), None, KNOWN, VENDOR, 0.5),
        MembershipChange("SX5E", "Q", date(2010, 1, 1), None, KNOWN, VENDOR, 0.3),
        MembershipChange("SX5E", "R", date(2010, 1, 1), None, KNOWN, VENDOR, 0.2),
    )
    ingest_membership_changes(store, good, complete_snapshot=True)
    assert {r.constituent for r in store.read("index_constituents")} == {"P", "Q", "R"}


def test_incremental_load_with_missing_weight_skips_the_sum_check(tmp_path: Path) -> None:
    store = _store(tmp_path)
    partial = (
        MembershipChange("SX5E", "P", date(2010, 1, 1), None, KNOWN, VENDOR, 0.25),
        MembershipChange("SX5E", "Q", date(2010, 1, 1), None, KNOWN, VENDOR, None),
    )
    ingest_membership_changes(store, partial)
    assert {r.constituent for r in store.read("index_constituents")} == {"P", "Q"}


def test_partial_weight_basket_is_labeled_unavailable_not_zeroed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    changes = (
        MembershipChange("SX5E", "P", date(2010, 1, 1), None, KNOWN, VENDOR, 0.40),
        MembershipChange("SX5E", "Q", date(2010, 1, 1), None, KNOWN, VENDOR, None),
    )
    ingest_membership_changes(store, changes)
    basket = members(store, "SX5E", date(2020, 1, 1))
    weights = {m.constituent: m.weight for m in basket}
    assert weights == {"P": 0.40, "Q": None}
    assert basket_weight_sum(basket) is None


def test_restatement_does_not_erase_what_was_known_earlier(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = (
        MembershipChange("SX5E", "A", date(2010, 1, 1), None, date(2020, 1, 1), VENDOR, 0.5),
    )
    ingest_membership_changes(store, original)
    restated = (
        MembershipChange(
            "SX5E", "A", date(2010, 1, 1), date(2020, 6, 1), date(2023, 1, 1), VENDOR, 0.5
        ),
    )
    ingest_membership_changes(store, restated)

    probe = date(2020, 7, 1)
    assert _names(store, probe, known_as_of=date(2020, 12, 31)) == ("A",)
    assert _names(store, probe, known_as_of=date(2023, 2, 1)) == ()
    assert _names(store, probe) == ()


def test_restatement_lands_as_a_new_knowledge_axis_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(
        store,
        (MembershipChange("SX5E", "A", date(2010, 1, 1), None, date(2020, 1, 1), VENDOR, 0.5),),
    )
    ingest_membership_changes(
        store,
        (
            MembershipChange(
                "SX5E", "A", date(2010, 1, 1), date(2020, 6, 1), date(2023, 1, 1), VENDOR, 0.5
            ),
        ),
    )
    rows = [r for r in store.read("index_constituents") if r.constituent == "A"]
    assert len(rows) == 2
    assert {r.knowledge_date for r in rows} == {date(2020, 1, 1), date(2023, 1, 1)}


def test_index_constituent_round_trips_through_storage(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = IndexConstituent(
        index="SX5E",
        constituent="ASML",
        effective_add_date=date(2010, 1, 1),
        effective_remove_date=None,
        knowledge_date=date(2020, 1, 1),
        vendor=VENDOR,
        weight=0.1,
    )
    store.write("index_constituents", [record])
    [read_back] = store.read("index_constituents")
    assert read_back == record


def test_index_constituent_validates_against_its_schema(tmp_path: Path) -> None:
    record = IndexConstituent(
        index="SX5E",
        constituent="ASML",
        effective_add_date=date(2010, 1, 1),
        effective_remove_date=date(2021, 1, 1),
        knowledge_date=date(2020, 1, 1),
        vendor=VENDOR,
        weight=0.1,
    )
    spec = spec_for_table("index_constituents")
    assert spec.layer == "reference"
    assert spec.append_only is True
    assert spec.provider_partitioned is False
    validate_record("index_constituents", record)


@pytest.mark.parametrize(
    ("field", "change"),
    [
        (
            "weight",
            MembershipChange("SX5E", "A", date(2010, 1, 1), None, KNOWN, VENDOR, -0.1),
        ),
        (
            "effective_remove_date",
            MembershipChange(
                "SX5E", "A", date(2021, 1, 1), date(2020, 1, 1), KNOWN, VENDOR, 0.1
            ),
        ),
        (
            "index",
            MembershipChange("", "A", date(2010, 1, 1), None, KNOWN, VENDOR, 0.1),
        ),
        (
            "constituent",
            MembershipChange("SX5E", "", date(2010, 1, 1), None, KNOWN, VENDOR, 0.1),
        ),
    ],
)
def test_malformed_change_is_rejected_with_explicit_error(
    tmp_path: Path, field: str, change: MembershipChange
) -> None:
    store = _store(tmp_path)
    with pytest.raises(MembershipError) as exc:
        ingest_membership_changes(store, (change,))
    assert exc.value.field == field
    assert store.read("index_constituents") == []


def test_nonfinite_weight_is_rejected_by_contract_validation_directly() -> None:
    bad = IndexConstituent(
        index="SX5E",
        constituent="A",
        effective_add_date=date(2010, 1, 1),
        effective_remove_date=None,
        knowledge_date=date(2020, 1, 1),
        vendor=VENDOR,
        weight=float("nan"),
    )
    with pytest.raises(ContractValidationError):
        validate_record("index_constituents", bad)


def test_unknown_index_yields_empty_basket(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    assert members(store, "NOPE", date(2020, 1, 1)) == ()


def test_empty_store_yields_empty_basket(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert members(store, "SX5E", date(2020, 1, 1)) == ()


def test_single_constituent_index(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(
        store,
        (MembershipChange("SX5E", "ONLY", date(2010, 1, 1), None, KNOWN, VENDOR, 1.0),),
    )
    assert members(store, "SX5E", date(2020, 1, 1)) == (BasketMember("ONLY", 1.0),)


def test_date_before_earliest_record_is_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    assert members(store, "SX5E", date(2009, 1, 1)) == ()


def test_open_ended_member_resolves_for_a_far_future_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(
        store,
        (MembershipChange("SX5E", "FOREVER", date(2010, 1, 1), None, KNOWN, VENDOR, None),),
    )
    assert _names(store, date(2030, 12, 31)) == ("FOREVER",)


def test_empty_ingest_is_a_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert ingest_membership_changes(store, ()) == ()
    assert store.read("index_constituents") == []


def test_shuffled_ingest_yields_same_membership_and_baskets(tmp_path: Path) -> None:
    store_a = ParquetStore(tmp_path / "a")
    store_b = ParquetStore(tmp_path / "b")
    changes = list(SX5E_CHANGES)
    ingest_membership_changes(store_a, changes)
    shuffled = changes[:]
    random.Random(20260607).shuffle(shuffled)
    assert shuffled != changes
    ingest_membership_changes(store_b, shuffled)

    def _canon(store: ParquetStore) -> list[tuple[object, ...]]:
        rows = store.read("index_constituents")
        return sorted(
            (r.index, r.constituent, r.effective_add_date, r.knowledge_date, r.vendor, r.weight)
            for r in rows
        )

    assert _canon(store_a) == _canon(store_b)
    for probe in (date(2019, 6, 30), date(2021, 1, 15), date(2022, 3, 1)):
        assert members(store_a, "SX5E", probe) == members(store_b, "SX5E", probe)


def test_resolver_output_is_sorted_by_constituent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    changes = (
        MembershipChange("SX5E", "ZED", date(2010, 1, 1), None, KNOWN, VENDOR, 0.5),
        MembershipChange("SX5E", "ABLE", date(2010, 1, 1), None, KNOWN, VENDOR, 0.5),
        MembershipChange("SX5E", "MID", date(2010, 1, 1), None, KNOWN, VENDOR, 0.0),
    )
    ingest_membership_changes(store, changes)
    names = _names(store, date(2020, 1, 1))
    assert names == tuple(sorted(names)) == ("ABLE", "MID", "ZED")


def test_reingest_same_change_is_a_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    before = sorted((r.constituent, r.knowledge_date) for r in store.read("index_constituents"))
    ingest_membership_changes(store, SX5E_CHANGES)
    after = sorted((r.constituent, r.knowledge_date) for r in store.read("index_constituents"))
    assert before == after


def test_conflicting_payload_under_same_key_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(
        store,
        (MembershipChange("SX5E", "A", date(2010, 1, 1), None, date(2020, 1, 1), VENDOR, 0.5),),
    )
    with pytest.raises((MembershipError, AppendOnlyViolation)):
        ingest_membership_changes(
            store,
            (
                MembershipChange(
                    "SX5E", "A", date(2010, 1, 1), None, date(2020, 1, 1), VENDOR, 0.9
                ),
            ),
        )


def test_sp500_resolves_on_the_same_contract_and_resolver(tmp_path: Path) -> None:
    store = _store(tmp_path)
    changes = (
        MembershipChange("SPX", "AAPL", date(2010, 1, 1), None, KNOWN, "EODHD", None),
        MembershipChange("SPX", "TSLA", date(2020, 12, 21), None, KNOWN, "EODHD", None),
        MembershipChange("SPX", "XRX", date(2010, 1, 1), date(2014, 6, 1), KNOWN, "EODHD", None),
    )
    ingest_membership_changes(store, changes)
    assert tuple(m.constituent for m in members(store, "SPX", date(2013, 1, 1))) == ("AAPL", "XRX")
    assert tuple(m.constituent for m in members(store, "SPX", date(2021, 1, 1))) == ("AAPL", "TSLA")
    ingest_membership_changes(store, SX5E_CHANGES)
    assert "AAPL" not in _names(store, date(2021, 1, 15))
