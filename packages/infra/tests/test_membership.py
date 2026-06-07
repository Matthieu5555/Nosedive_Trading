"""WS 1A — point-in-time index membership: the look-ahead-critical resolver.

The expected baskets below are derived **independently of the code under test** (per
TESTING.md): real dated EURO STOXX 50 (SX5E) membership changes are hand-encoded in the
fixtures, and the basket for each probe date is computed *by hand* in the test comment from
those changes — never by calling the resolver and asserting it equals itself.

The load-bearing case is the negative one: the *current* basket differs from a *past* basket,
and ``members(index, past_date)`` must return the past basket and never fall back to the
latest membership (the only thing that proves there is no look-ahead).

Real SX5E review history used (public STOXX index review record):
  * 2020-09-21 review: ``ADYEN`` (Adyen) was **added** to the SX5E.
  * 2021-09-20 review: ``ENGI`` (Engie) was **removed** from the SX5E.
  * ``ASML`` is a long-standing member, present across the whole window (the "always in" name).
The exact corporate names are immaterial to the test; what matters is one add and one removal
on known dates, so the included set on either side of each event is hand-computable.
"""

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

# --- the hand-encoded SX5E fixture: one always-in name, one add, one removal -------------
# Effective intervals (half-open [add, remove)), all recorded on one early knowledge date so
# the knowledge axis is constant here (its own tests are below):
#   ASML  : [2010-01-01, None)             -> in for every probe date
#   ADYEN : [2020-09-21, None)             -> added at the 2020-09-21 review
#   ENGI  : [2010-01-01, 2021-09-20)       -> removed at the 2021-09-20 review
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


# --- as-of basket correctness (the load-bearing case) -----------------------------------


def test_basket_before_addition_excludes_the_added_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    # 2019-06-30 is before ADYEN's 2020-09-21 add and before ENGI's 2021 removal:
    # hand-computed basket = {ASML, ENGI}; ADYEN excluded.
    assert _names(store, date(2019, 6, 30)) == ("ASML", "ENGI")


def test_basket_after_addition_before_removal_includes_both(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    # 2021-01-15 is after ADYEN's add and before ENGI's removal:
    # hand-computed basket = {ADYEN, ASML, ENGI}.
    assert _names(store, date(2021, 1, 15)) == ("ADYEN", "ASML", "ENGI")


def test_basket_after_removal_excludes_the_removed_name(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    # 2022-03-01 is after ENGI's 2021-09-20 removal:
    # hand-computed basket = {ADYEN, ASML}; ENGI excluded.
    assert _names(store, date(2022, 3, 1)) == ("ADYEN", "ASML")


# --- no-lookahead boundary: half-open [add, remove), both ends pinned --------------------


def test_added_name_is_in_on_its_exact_add_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    # Half-open convention: a name IS in the basket on its effective_add_date.
    # On 2020-09-21 (ADYEN's add date) the basket = {ADYEN, ASML, ENGI}.
    assert "ADYEN" in _names(store, date(2020, 9, 21))
    assert _names(store, date(2020, 9, 21)) == ("ADYEN", "ASML", "ENGI")


def test_added_name_is_out_the_day_before_its_add_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    # The day before the add date, the name is out (boundary, off-by-one guard).
    assert "ADYEN" not in _names(store, date(2020, 9, 20))


def test_removed_name_is_out_on_its_exact_remove_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    # Half-open convention: a name removed on date D is ABSENT for D.
    # ENGI's effective_remove_date is 2021-09-20, so it is out on 2021-09-20.
    assert "ENGI" not in _names(store, date(2021, 9, 20))


def test_removed_name_is_in_the_day_before_its_remove_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    # The day before removal, the name is still in (the other boundary end).
    assert "ENGI" in _names(store, date(2021, 9, 19))


# --- today's-list-is-not-history guard (the direct negative assertion) -------------------


def test_past_basket_is_not_the_current_basket(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    # Current basket (a recent date) differs from a 2019 basket: ADYEN joined later, ENGI left.
    current = _names(store, date(2024, 1, 2))  # hand-computed = {ADYEN, ASML}
    past = _names(store, date(2019, 6, 30))  # hand-computed = {ASML, ENGI}
    assert current == ("ADYEN", "ASML")
    assert past == ("ASML", "ENGI")
    # The direct negative: the past resolve does NOT return the current list, and in
    # particular it includes ENGI (gone today) and excludes ADYEN (here today).
    assert past != current
    assert "ENGI" in past and "ENGI" not in current
    assert "ADYEN" not in past and "ADYEN" in current


# --- weights as-of ----------------------------------------------------------------------


def test_weight_resolves_per_date_when_it_changes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # A name's weight changes between two intervals: 0.50 until 2021-01-01, then 0.60.
    # Modeled as the change-row form: close the old interval, open a new one.
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
    # A complete-weight basket whose weights sum to 1.0 by construction.
    changes = (
        MembershipChange("SX5E", "P", date(2010, 1, 1), None, KNOWN, VENDOR, 0.40),
        MembershipChange("SX5E", "Q", date(2010, 1, 1), None, KNOWN, VENDOR, 0.35),
        MembershipChange("SX5E", "R", date(2010, 1, 1), None, KNOWN, VENDOR, 0.25),
    )
    ingest_membership_changes(store, changes)
    basket = members(store, "SX5E", date(2020, 1, 1))
    total = basket_weight_sum(basket)
    assert total is not None
    # Hand sum = 0.40 + 0.35 + 0.25 = 1.00; float comparison with an explicit tolerance.
    assert total == pytest.approx(1.0, abs=1e-9)


def test_corrupt_complete_weight_snapshot_is_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # A declared complete snapshot whose weights sum to 0.50 — far from 1.0. This is a corrupt
    # load and is rejected on write (hand sum: 0.25 + 0.25 = 0.50, |0.50 - 1.0| > tolerance).
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
    # A "complete" snapshot cannot have a labeled-unavailable weight — that contradicts
    # "complete". The loader is told it is incomplete rather than the None being zeroed.
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
    # Hand sum: 0.5 + 0.3 + 0.2 = 1.0 — a valid complete snapshot, accepted.
    good = (
        MembershipChange("SX5E", "P", date(2010, 1, 1), None, KNOWN, VENDOR, 0.5),
        MembershipChange("SX5E", "Q", date(2010, 1, 1), None, KNOWN, VENDOR, 0.3),
        MembershipChange("SX5E", "R", date(2010, 1, 1), None, KNOWN, VENDOR, 0.2),
    )
    ingest_membership_changes(store, good, complete_snapshot=True)  # does not raise
    assert {r.constituent for r in store.read("index_constituents")} == {"P", "Q", "R"}


def test_incremental_load_with_missing_weight_skips_the_sum_check(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # The default (incremental) load is a partial/labeled source: a missing weight is fine and
    # the sum check does not apply, so this ingests cleanly even though weights are partial.
    partial = (
        MembershipChange("SX5E", "P", date(2010, 1, 1), None, KNOWN, VENDOR, 0.25),
        MembershipChange("SX5E", "Q", date(2010, 1, 1), None, KNOWN, VENDOR, None),
    )
    ingest_membership_changes(store, partial)  # default complete_snapshot=False, no raise
    assert {r.constituent for r in store.read("index_constituents")} == {"P", "Q"}


def test_partial_weight_basket_is_labeled_unavailable_not_zeroed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Source gives a weight for one name and not the other; the missing one is None,
    # never silently 0.0 — so the basket sum is "not assertable" (None), not a wrong total.
    changes = (
        MembershipChange("SX5E", "P", date(2010, 1, 1), None, KNOWN, VENDOR, 0.40),
        MembershipChange("SX5E", "Q", date(2010, 1, 1), None, KNOWN, VENDOR, None),
    )
    ingest_membership_changes(store, changes)
    basket = members(store, "SX5E", date(2020, 1, 1))
    weights = {m.constituent: m.weight for m in basket}
    assert weights == {"P": 0.40, "Q": None}
    assert basket_weight_sum(basket) is None  # not 0.40, not 0.0


# --- bitemporal restatement -------------------------------------------------------------


def test_restatement_does_not_erase_what_was_known_earlier(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Originally (known 2020-01-01): name A is an open-ended member from 2010.
    original = (
        MembershipChange("SX5E", "A", date(2010, 1, 1), None, date(2020, 1, 1), VENDOR, 0.5),
    )
    ingest_membership_changes(store, original)
    # Later (known 2023-01-01): a vendor restates that A actually left on 2020-06-01.
    restated = (
        MembershipChange(
            "SX5E", "A", date(2010, 1, 1), date(2020, 6, 1), date(2023, 1, 1), VENDOR, 0.5
        ),
    )
    ingest_membership_changes(store, restated)

    probe = date(2020, 7, 1)
    # As the data was known on 2020-12-31 (before the restatement): A was still believed
    # to be a member on 2020-07-01 — the original open-ended interval.
    assert _names(store, probe, known_as_of=date(2020, 12, 31)) == ("A",)
    # As known on 2023-02-01 (after the restatement): A had left on 2020-06-01, so it is
    # out for 2020-07-01 — the corrected history.
    assert _names(store, probe, known_as_of=date(2023, 2, 1)) == ()
    # Default (latest knowledge) follows the most recent restatement.
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
    # Two physical rows for the same effective interval, one per knowledge_date — the
    # original is preserved, not overwritten (bitemporal immutability).
    assert len(rows) == 2
    assert {r.knowledge_date for r in rows} == {date(2020, 1, 1), date(2023, 1, 1)}


# --- contract round-trip / seam (A-side discipline) -------------------------------------


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
    # Registered, frozen, provider-agnostic reference-layer contract.
    spec = spec_for_table("index_constituents")
    assert spec.layer == "reference"
    assert spec.append_only is True
    assert spec.provider_partitioned is False
    validate_record("index_constituents", record)  # does not raise


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
    # Nothing is written when a change in the batch is rejected.
    assert store.read("index_constituents") == []


def test_nonfinite_weight_is_rejected_by_contract_validation_directly() -> None:
    # The storage write door rejects a non-finite weight independently of the ingester:
    # weight is an optional numeric, so None is allowed but NaN/inf is not — the contract
    # layer's finite-number check catches it before any byte is written.
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


# --- edge cases (TESTING.md floor) ------------------------------------------------------


def test_unknown_index_yields_empty_basket(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    assert members(store, "NOPE", date(2020, 1, 1)) == ()


def test_empty_store_yields_empty_basket(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # No membership ingested at all: a labeled empty basket, never a crash.
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
    # Earliest add is 2010-01-01; a 2009 probe is before any record → empty, labeled.
    assert members(store, "SX5E", date(2009, 1, 1)) == ()


def test_open_ended_member_resolves_for_a_far_future_date(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(
        store,
        (MembershipChange("SX5E", "FOREVER", date(2010, 1, 1), None, KNOWN, VENDOR, None),),
    )
    # A never-removed (open-ended) member is in for any date at/after its add.
    assert _names(store, date(2030, 12, 31)) == ("FOREVER",)


def test_empty_ingest_is_a_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert ingest_membership_changes(store, ()) == ()
    assert store.read("index_constituents") == []


# --- determinism / reordering invariance ------------------------------------------------


def test_shuffled_ingest_yields_same_membership_and_baskets(tmp_path: Path) -> None:
    store_a = ParquetStore(tmp_path / "a")
    store_b = ParquetStore(tmp_path / "b")
    changes = list(SX5E_CHANGES)
    ingest_membership_changes(store_a, changes)
    shuffled = changes[:]
    random.Random(20260607).shuffle(shuffled)
    assert shuffled != changes  # the shuffle actually reordered the input
    ingest_membership_changes(store_b, shuffled)

    # Same on-disk membership (compared as canonical sorted sets of contracts).
    def _canon(store: ParquetStore) -> list[tuple[object, ...]]:
        rows = store.read("index_constituents")
        return sorted(
            (r.index, r.constituent, r.effective_add_date, r.knowledge_date, r.vendor, r.weight)
            for r in rows
        )

    assert _canon(store_a) == _canon(store_b)
    # Same resolved baskets across several probe dates.
    for probe in (date(2019, 6, 30), date(2021, 1, 15), date(2022, 3, 1)):
        assert members(store_a, "SX5E", probe) == members(store_b, "SX5E", probe)


def test_resolver_output_is_sorted_by_constituent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # Ingest in deliberately non-alphabetical order; the basket must come back sorted.
    changes = (
        MembershipChange("SX5E", "ZED", date(2010, 1, 1), None, KNOWN, VENDOR, 0.5),
        MembershipChange("SX5E", "ABLE", date(2010, 1, 1), None, KNOWN, VENDOR, 0.5),
        MembershipChange("SX5E", "MID", date(2010, 1, 1), None, KNOWN, VENDOR, 0.0),
    )
    ingest_membership_changes(store, changes)
    names = _names(store, date(2020, 1, 1))
    assert names == tuple(sorted(names)) == ("ABLE", "MID", "ZED")


# --- re-ingest idempotency / immutability ----------------------------------------------


def test_reingest_same_change_is_a_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(store, SX5E_CHANGES)
    before = sorted((r.constituent, r.knowledge_date) for r in store.read("index_constituents"))
    ingest_membership_changes(store, SX5E_CHANGES)  # same batch again
    after = sorted((r.constituent, r.knowledge_date) for r in store.read("index_constituents"))
    assert before == after


def test_conflicting_payload_under_same_key_is_refused(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ingest_membership_changes(
        store,
        (MembershipChange("SX5E", "A", date(2010, 1, 1), None, date(2020, 1, 1), VENDOR, 0.5),),
    )
    # Same bitemporal key, different payload (a different weight) — must be refused, not a
    # silent overwrite of immutable history. A restatement must carry a new knowledge_date.
    with pytest.raises((MembershipError, AppendOnlyViolation)):
        ingest_membership_changes(
            store,
            (
                MembershipChange(
                    "SX5E", "A", date(2010, 1, 1), None, date(2020, 1, 1), VENDOR, 0.9
                ),
            ),
        )


# --- SP500 stretch goal: same contract, same resolver, no second code path --------------


def test_sp500_resolves_on_the_same_contract_and_resolver(tmp_path: Path) -> None:
    store = _store(tmp_path)
    # A small SP500 slice proving the same resolver answers a second index with no new code.
    changes = (
        MembershipChange("SPX", "AAPL", date(2010, 1, 1), None, KNOWN, "EODHD", None),
        MembershipChange("SPX", "TSLA", date(2020, 12, 21), None, KNOWN, "EODHD", None),
        MembershipChange("SPX", "XRX", date(2010, 1, 1), date(2014, 6, 1), KNOWN, "EODHD", None),
    )
    ingest_membership_changes(store, changes)
    # 2013: XRX still in, TSLA not yet → {AAPL, XRX}.
    assert tuple(m.constituent for m in members(store, "SPX", date(2013, 1, 1))) == ("AAPL", "XRX")
    # 2021: TSLA added, XRX gone → {AAPL, TSLA}.
    assert tuple(m.constituent for m in members(store, "SPX", date(2021, 1, 1))) == ("AAPL", "TSLA")
    # SX5E and SPX coexist in one store without cross-talk.
    ingest_membership_changes(store, SX5E_CHANGES)
    assert "AAPL" not in _names(store, date(2021, 1, 15))
