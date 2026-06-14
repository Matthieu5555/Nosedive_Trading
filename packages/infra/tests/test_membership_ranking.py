"""S1 precondition — the point-in-time top-N-by-weight selector (`top_n_by_weight`).

The expected rankings below are derived **independently of the code under test** (per
TESTING.md): a small hand-encoded SX5E basket with chosen weights, where the correct
descending-weight order and the correct tie-break are computed *by hand* in each test comment,
never by calling the resolver and asserting it equals itself.

`top_n_by_weight` adds only a ranking on top of `members` (the look-ahead-gated as-of resolver,
exercised in `test_membership.py`); so these tests focus on the ranking contract — descending
weight, ascending-symbol tie-break, the N slice, the as-of plumbing, and the two labeled
refusals (non-positive N; a basket with any unavailable weight).
"""

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

# The committed SX5E weighted snapshot (SSGA SPDR FEZ holdings) the resolver consumes in prod.
_SX5E_WEIGHTS_CSV = (
    Path(__file__).resolve().parents[3]
    / "configs"
    / "index_weights"
    / "sx5e_ssga_fez_2026-06-09.csv"
)

VENDOR = "SSGA:FEZ-test"
KNOWN = date(2024, 1, 1)
AS_OF = date(2024, 6, 1)

# --- a hand-encoded weighted SX5E basket --------------------------------------------------
# All five names are members on AS_OF (each added before it, none removed). Weights are in
# percent (the SSGA SPDR-ETF feed's units), chosen so the descending order is unambiguous and
# so two names (DEF, GHI) share a weight to exercise the tie-break:
#   ASML  weight 12.0   -> rank 1 (heaviest)
#   SAP    weight  4.0   -> rank 2
#   DEF    weight  3.0   \  tie at 3.0 -> ascending symbol breaks it: DEF before GHI
#   GHI    weight  3.0   /
#   ABC    weight  1.0   -> rank 5 (lightest)
# Hand-computed full descending order (weight desc, then symbol asc):
#   [ASML(12.0), SAP(4.0), DEF(3.0), GHI(3.0), ABC(1.0)]
WEIGHTED = (
    MembershipChange("SX5E", "ASML", date(2020, 1, 1), None, KNOWN, VENDOR, 12.0),
    MembershipChange("SX5E", "SAP", date(2020, 1, 1), None, KNOWN, VENDOR, 4.0),
    MembershipChange("SX5E", "DEF", date(2020, 1, 1), None, KNOWN, VENDOR, 3.0),
    MembershipChange("SX5E", "GHI", date(2020, 1, 1), None, KNOWN, VENDOR, 3.0),
    MembershipChange("SX5E", "ABC", date(2020, 1, 1), None, KNOWN, VENDOR, 1.0),
)
# The full hand-derived ranking (used to slice expected top-N below).
RANKED = (
    BasketMember("ASML", 12.0),
    BasketMember("SAP", 4.0),
    BasketMember("DEF", 3.0),
    BasketMember("GHI", 3.0),
    BasketMember("ABC", 1.0),
)


def _store(tmp_path: Path) -> ParquetStore:
    return ParquetStore(tmp_path)


# --- the ranking contract -----------------------------------------------------------------


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
    # DEF and GHI both weigh 3.0; the deterministic tie-break is ascending symbol, so DEF
    # precedes GHI in the top-4 (hand-derived above). A top-3 keeps only DEF of the tied pair.
    store = _store(tmp_path)
    ingest_membership_changes(store, WEIGHTED)
    top4 = top_n_by_weight(store, "SX5E", AS_OF, 4)
    assert [m.constituent for m in top4] == ["ASML", "SAP", "DEF", "GHI"]
    top3 = top_n_by_weight(store, "SX5E", AS_OF, 3)
    assert [m.constituent for m in top3] == ["ASML", "SAP", "DEF"]


def test_n_larger_than_the_basket_returns_all_members_not_padded(tmp_path: Path) -> None:
    # The basket has 5 names; asking for the top-50 (the SX5E theory size) yields all 5 in
    # ranked order — a smaller live index is legitimate, never an error or a padded result.
    store = _store(tmp_path)
    ingest_membership_changes(store, WEIGHTED)
    result = top_n_by_weight(store, "SX5E", AS_OF, 50)
    assert result == RANKED
    assert len(result) == 5


def test_ranking_is_independent_of_ingest_order(tmp_path: Path) -> None:
    # Ingesting the changes in reverse order must not change the resolved ranking — the
    # selector sorts, it does not rely on storage order.
    store = _store(tmp_path)
    ingest_membership_changes(store, tuple(reversed(WEIGHTED)))
    assert top_n_by_weight(store, "SX5E", AS_OF, 5) == RANKED


# --- the as-of plumbing (look-ahead: the rank is taken on the as-of basket) ---------------


def test_top_n_ranks_the_point_in_time_basket_not_todays(tmp_path: Path) -> None:
    # A name added *after* the probe date must not appear in the top-N for that date. ZZZ is the
    # heaviest name overall (weight 99) but only from 2025; on AS_OF (2024-06-01) it is not yet a
    # member, so the top-1 must still be ASML (12.0), not ZZZ. This is the look-ahead guard: the
    # rank is taken on the basket as it stood on AS_OF, never on the latest membership.
    store = _store(tmp_path)
    later = MembershipChange("SX5E", "ZZZ", date(2025, 1, 1), None, KNOWN, VENDOR, 99.0)
    ingest_membership_changes(store, (*WEIGHTED, later))
    assert top_n_by_weight(store, "SX5E", AS_OF, 1) == (BasketMember("ASML", 12.0),)
    # On a 2025 date ZZZ is a member and, being heaviest, takes rank 1.
    assert top_n_by_weight(store, "SX5E", date(2025, 6, 1), 1) == (BasketMember("ZZZ", 99.0),)


def test_empty_basket_returns_empty_not_an_error(tmp_path: Path) -> None:
    # An unknown index (nothing ingested for it) has nothing to rank: a labeled empty result,
    # not a crash and not a ranking error.
    store = _store(tmp_path)
    ingest_membership_changes(store, WEIGHTED)
    assert top_n_by_weight(store, "UNKNOWN_INDEX", AS_OF, 10) == ()


def test_date_before_history_returns_empty(tmp_path: Path) -> None:
    # Before the earliest add date the basket is empty (no members yet), so the top-N is empty.
    store = _store(tmp_path)
    ingest_membership_changes(store, WEIGHTED)
    assert top_n_by_weight(store, "SX5E", date(2019, 1, 1), 5) == ()


# --- the two labeled refusals -------------------------------------------------------------


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
    # MISS carries no weight (None). You cannot rank what isn't known: silently dropping or
    # zeroing it would bias the selection, so the selector refuses with a labeled error naming
    # the unweighted name — never a quietly-truncated top-N.
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
    # the offending name is surfaced in the labeled value
    assert "MISS" in str(exc.value.value)


def test_known_as_of_selects_the_knowledge_vintage_for_the_rank(tmp_path: Path) -> None:
    # Knowledge axis: a later restatement reweights ASML, but a rank "as known on the early
    # date" must use the early weight. Two names, no tie, so the order is weight-driven.
    #   knowledge 2024-01-01: ASML 12.0, SAP 4.0   -> ASML, SAP
    #   knowledge 2024-07-01 (restated):  ASML 1.0, SAP 4.0  -> SAP, ASML (ASML now lighter)
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
    # As known on 2024-01-01: ASML heavier -> rank 1 ASML.
    known_early = top_n_by_weight(store, "SX5E", AS_OF, 1, known_as_of=date(2024, 1, 1))
    assert known_early == (BasketMember("ASML", 12.0),)
    # With the latest knowledge (no known_as_of): ASML restated to 1.0 -> SAP now rank 1.
    latest = top_n_by_weight(store, "SX5E", AS_OF, 1)
    assert latest == (BasketMember("SAP", 4.0),)


# --- the shipped weighted SX5E source, end to end (CsvFileSource -> ingest -> rank) -------


def test_shipped_sx5e_weighted_csv_ranks_through_the_resolver(tmp_path: Path) -> None:
    # Prove the actual committed weighted SX5E snapshot (SSGA SPDR FEZ holdings) flows through
    # the real source -> ingest -> top_n_by_weight path, against a TEMP store (never data/).
    # The top-5 are read by eye from the CSV's first rows (it is sorted heaviest-first):
    #   ASML 12.076038, SIE 4.570803, TTE 3.848558, SAP 3.634669, SU 3.496624
    # — independent of the resolver, which re-derives the order from the raw rows.
    source = CsvFileSource(
        path=_SX5E_WEIGHTS_CSV,
        vendor="SSGA:FEZ-2026-06-09",
        default_add_date=date(2020, 1, 1),
    )
    changes = source.fetch("SX5E", knowledge_date=date(2026, 6, 9))
    assert len(changes) == 50  # the SX5E theory basket size
    store = _store(tmp_path)
    ingest_membership_changes(store, changes)

    top5 = top_n_by_weight(store, "SX5E", date(2026, 6, 9), 5)
    assert [m.constituent for m in top5] == ["ASML", "SIE", "TTE", "SAP", "SU"]
    assert top5[0].weight == pytest.approx(12.076038)
    assert top5[1].weight == pytest.approx(4.570803)

    # The default (course top-10) and the theory top-50 both resolve; 50 == the whole basket.
    assert len(top_n_by_weight(store, "SX5E", date(2026, 6, 9), 10)) == 10
    assert len(top_n_by_weight(store, "SX5E", date(2026, 6, 9), 50)) == 50
