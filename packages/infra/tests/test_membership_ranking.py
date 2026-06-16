from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from algotrading.infra.storage import ParquetStore
from algotrading.infra.universe import (
    BasketMember,
    CsvFileSource,
    MembershipChange,
    MembershipRankingError,
    ingest_membership_changes,
    top_n_by_weight,
)

_SX5E_WEIGHTS_CSV = (
    Path(__file__).resolve().parents[3]
    / "configs"
    / "index_weights"
    / "sx5e_ssga_fez_2026-06-09.csv"
)

VENDOR = "SSGA:FEZ-test"
KNOWN = date(2024, 1, 1)
AS_OF = date(2024, 6, 1)

WEIGHTED = (
    MembershipChange("SX5E", "ASML", date(2020, 1, 1), None, KNOWN, VENDOR, 12.0),
    MembershipChange("SX5E", "SAP", date(2020, 1, 1), None, KNOWN, VENDOR, 4.0),
    MembershipChange("SX5E", "DEF", date(2020, 1, 1), None, KNOWN, VENDOR, 3.0),
    MembershipChange("SX5E", "GHI", date(2020, 1, 1), None, KNOWN, VENDOR, 3.0),
    MembershipChange("SX5E", "ABC", date(2020, 1, 1), None, KNOWN, VENDOR, 1.0),
)
RANKED = (
    BasketMember("ASML", 12.0),
    BasketMember("SAP", 4.0),
    BasketMember("DEF", 3.0),
    BasketMember("GHI", 3.0),
    BasketMember("ABC", 1.0),
)


def _store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(tmp_path)


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        pytest.param(1, RANKED[:1], id="top-1"),
        pytest.param(2, RANKED[:2], id="top-2"),
        pytest.param(3, RANKED[:3], id="top-3-includes-first-tie"),
        pytest.param(4, RANKED[:4], id="top-4-spans-the-tie"),
        pytest.param(5, RANKED[:5], id="top-5-whole-basket"),
    ],
)
def test_top_n_returns_the_heaviest_names_in_descending_weight_order(
    tmp_path: Path, n: int, expected: tuple[BasketMember, ...]
) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, WEIGHTED)
    assert top_n_by_weight(store, "SX5E", AS_OF, n) == expected


def test_tie_is_broken_by_ascending_symbol(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, WEIGHTED)
    top4 = top_n_by_weight(store, "SX5E", AS_OF, 4)
    assert [m.constituent for m in top4] == ["ASML", "SAP", "DEF", "GHI"]
    top3 = top_n_by_weight(store, "SX5E", AS_OF, 3)
    assert [m.constituent for m in top3] == ["ASML", "SAP", "DEF"]


def test_n_larger_than_the_basket_returns_all_members_not_padded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, WEIGHTED)
    result = top_n_by_weight(store, "SX5E", AS_OF, 50)
    assert result == RANKED
    assert len(result) == 5


def test_ranking_is_independent_of_ingest_order(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, tuple(reversed(WEIGHTED)))
    assert top_n_by_weight(store, "SX5E", AS_OF, 5) == RANKED


def test_top_n_ranks_the_point_in_time_basket_not_todays(tmp_path: Path) -> None:
    store = _store(tmp_path)
    later = MembershipChange("SX5E", "ZZZ", date(2025, 1, 1), None, KNOWN, VENDOR, 99.0)
    ingest_membership_changes(store, (*WEIGHTED, later))
    assert top_n_by_weight(store, "SX5E", AS_OF, 1) == (BasketMember("ASML", 12.0),)
    assert top_n_by_weight(store, "SX5E", date(2025, 6, 1), 1) == (BasketMember("ZZZ", 99.0),)


def test_empty_basket_returns_empty_not_an_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, WEIGHTED)
    assert top_n_by_weight(store, "UNKNOWN_INDEX", AS_OF, 10) == ()


def test_date_before_history_returns_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, WEIGHTED)
    assert top_n_by_weight(store, "SX5E", date(2019, 1, 1), 5) == ()


@pytest.mark.parametrize("bad_n", [0, -1, -10])
def test_non_positive_n_is_a_labeled_error(tmp_path: Path, bad_n: int) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, WEIGHTED)
    with pytest.raises(MembershipRankingError) as exc:
        top_n_by_weight(store, "SX5E", AS_OF, bad_n)
    assert exc.value.field == "n"
    assert exc.value.value == bad_n
    assert exc.value.index == "SX5E"


def test_basket_with_any_unavailable_weight_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    partial = (
        MembershipChange("SX5E", "ASML", date(2020, 1, 1), None, KNOWN, VENDOR, 12.0),
        MembershipChange("SX5E", "MISS", date(2020, 1, 1), None, KNOWN, VENDOR, None),
    )
    ingest_membership_changes(store, partial)
    with pytest.raises(MembershipRankingError) as exc:
        top_n_by_weight(store, "SX5E", AS_OF, 2)
    assert exc.value.field == "weight"
    assert exc.value.index == "SX5E"
    assert "MISS" in str(exc.value.value)


def test_known_as_of_selects_the_knowledge_vintage_for_the_rank(tmp_path: Path) -> None:
    store = _store(tmp_path)
    early = (
        MembershipChange("SX5E", "ASML", date(2020, 1, 1), None, date(2024, 1, 1), VENDOR, 12.0),
        MembershipChange("SX5E", "SAP", date(2020, 1, 1), None, date(2024, 1, 1), VENDOR, 4.0),
    )
    restated = MembershipChange(
        "SX5E", "ASML", date(2020, 1, 1), None, date(2024, 7, 1), VENDOR, 1.0
    )
    ingest_membership_changes(store, early)
    ingest_membership_changes(store, (restated,))
    known_early = top_n_by_weight(store, "SX5E", AS_OF, 1, known_as_of=date(2024, 1, 1))
    assert known_early == (BasketMember("ASML", 12.0),)
    latest = top_n_by_weight(store, "SX5E", AS_OF, 1)
    assert latest == (BasketMember("SAP", 4.0),)


def test_shipped_sx5e_weighted_csv_ranks_through_the_resolver(tmp_path: Path) -> None:
    source = CsvFileSource(
        path=_SX5E_WEIGHTS_CSV,
        vendor="SSGA:FEZ-2026-06-09",
        default_add_date=date(2020, 1, 1),
    )
    changes = source.fetch("SX5E", knowledge_date=date(2026, 6, 9))
    assert len(changes) == 50
    store = _store(tmp_path)
    ingest_membership_changes(store, changes)

    top5 = top_n_by_weight(store, "SX5E", date(2026, 6, 9), 5)
    assert [m.constituent for m in top5] == ["ASML", "SIE", "TTE", "SAP", "SU"]
    assert top5[0].weight == pytest.approx(12.076038)
    assert top5[1].weight == pytest.approx(4.570803)

    assert len(top_n_by_weight(store, "SX5E", date(2026, 6, 9), 10)) == 10
    assert len(top_n_by_weight(store, "SX5E", date(2026, 6, 9), 50)) == 50
