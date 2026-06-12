"""T-tenor-selection — the tenor-targeted (bracket) expiry policy.

The capture used to keep the **nearest N** expirations (``select_expiries``), which collapses
onto the front month whenever it lists N weeklies — so the fitted surface spanned ~2 weeks and
the projection onto 1m…3y had nothing to interpolate. These tests pin the replacement: for each
pinned tenor, keep the listed expiries **straddling** ``as_of + tenor·365`` so the captured chain
spans the term structure.

Independent oracle (TESTING.md "never test code against itself"): every expected bracket is the
set of listed dates I hand-pick as straddling a hand-computed target date — never a value read
back from the function under test. The ACT/365 target arithmetic is verified against dates
computed by hand in the test (accounting for the 2028 leap day), not by calling
``tenor_target_dates`` to check itself.
"""

from __future__ import annotations

from datetime import date

import pytest
from algotrading.infra.contracts import InstrumentKey
from algotrading.infra.universe import (
    AvailableChain,
    ChainSelection,
    bracket_dates,
    plan_chain,
    select_capture_keys,
    select_expiries,
    select_expiries_bracketing,
    tenor_target_dates,
)

# The pinned grid's ACT/365 year fractions (surfaces.projection._TENOR_YEARS), inlined so the
# expected dates below are checkable without importing the projection layer into a universe test.
_TENOR_YEARS = (10.0 / 365.0, 1.0 / 12.0, 3.0 / 12.0, 6.0 / 12.0, 1.0, 1.5, 2.0, 3.0)
_AS_OF = date(2026, 6, 11)


def test_tenor_target_dates_are_act365_from_as_of() -> None:
    """Each target is ``as_of + round(tenor·365)`` days (ACT/365, banker's rounding).

    The independent oracle is the **day count** from ``as_of`` — the ACT/365 rounding that is the
    actual logic — read back via ``(target - as_of).days`` (a different path from the timedelta the
    function adds). ``round`` is half-to-even, so 6m = round(182.5) = 182 and 18m = round(547.5) =
    548. The leap-day consequence is pinned separately: 3y = 1095 days lands on 2029-06-10, one day
    short of the 2029-06-11 anniversary because the 2028 leap day falls in the span.
    """
    got = tenor_target_dates(_AS_OF, _TENOR_YEARS)
    deltas = [(target - _AS_OF).days for target in got]
    assert deltas == [10, 30, 91, 182, 365, 548, 730, 1095]
    assert got[-1] == date(2029, 6, 10)  # 3y: 1095 days, short of the anniversary (2028 leap day)


def test_tenor_target_dates_skips_non_finite_and_dedups() -> None:
    """Non-finite / non-positive tenors are skipped; equal targets de-duplicate; sorted."""
    got = tenor_target_dates(_AS_OF, [0.25, 0.25, float("nan"), -1.0, 0.0])
    assert got == (date(2026, 9, 10),)


def test_bracket_straddles_target() -> None:
    """A target between two listed dates selects exactly the nearest below and nearest above."""
    listed = [date(2026, 6, 12), date(2026, 6, 19), date(2026, 9, 10), date(2026, 12, 18)]
    # target 2026-07-01 sits between 06-19 and 09-10 → those two, not 06-12 and not 12-18.
    assert bracket_dates(listed, [date(2026, 7, 1)]) == (date(2026, 6, 19), date(2026, 9, 10))


def test_bracket_on_listed_date_selects_that_one() -> None:
    """A target falling exactly on a listed date selects only that date (both bounds coincide)."""
    listed = [date(2026, 6, 12), date(2026, 9, 10), date(2026, 12, 18)]
    assert bracket_dates(listed, [date(2026, 9, 10)]) == (date(2026, 9, 10),)


def test_bracket_past_the_end_is_one_sided() -> None:
    """A target past the furthest listed date keeps only the below side — never empty, never raise."""
    listed = [date(2026, 6, 19), date(2027, 6, 18)]
    assert bracket_dates(listed, [date(2029, 6, 10)]) == (date(2027, 6, 18),)
    # And a target before the first keeps only the above side.
    assert bracket_dates(listed, [date(2025, 1, 1)]) == (date(2026, 6, 19),)


def test_select_expiries_bracketing_spans_term_structure_and_dedups() -> None:
    """The full grid over a real-shaped listing keeps a point either side of each tenor, deduped.

    Listing: a front weekly pair, then monthly/quarterly out to ~3.5y. Expected = the union of
    hand-picked brackets; adjacent short tenors share the front expiries so the union is far
    smaller than 2×(8 tenors).
    """
    listed = [
        "20260612", "20260619", "20260717", "20260918", "20261218",
        "20270618", "20271217", "20281215", "20291221",
    ]
    got = select_expiries_bracketing(listed, as_of=_AS_OF, tenor_years=_TENOR_YEARS)
    # Hand-derived: targets 06-21/07-11/09-10/12-09/2027-06-11/2027-12-09/2028-06-10/2029-06-10
    # bracket to this exact union over the listing above.
    assert got == (
        "20260619", "20260717", "20260918", "20261218",
        "20270618", "20271217", "20281215", "20291221",
    )
    assert got == tuple(sorted(set(got)))          # chronological, de-duplicated
    assert "20260612" not in got                   # the front weekly nearest-N would keep is dropped
    assert got[-1] == "20291221"                   # the 3y point is reached


def test_no_frontload_collapse_when_longer_expiries_exist() -> None:
    """The 2026-06-11 bug: a front month of 8 weeklies must NOT swallow the whole budget.

    With eight June weeklies plus real longer maturities, ``select_expiries`` (nearest 8) keeps
    only June; the bracket must reach the long end instead.
    """
    june = [f"202606{day:02d}" for day in (10, 11, 12, 15, 16, 17, 18, 22)]
    longer = ["20260918", "20261218", "20270618", "20281215", "20291221"]
    listed = june + longer
    nearest8 = select_expiries(listed, 8)
    assert all(token.startswith("202606") for token in nearest8)  # the bug: all June
    got = select_expiries_bracketing(
        listed, as_of=_AS_OF, tenor_years=(10 / 365, 1 / 12, 3 / 12, 6 / 12, 1.0, 2.0, 3.0)
    )
    assert any(token >= "20281215" for token in got)  # reaches 2y/3y
    assert got[-1] == "20291221"


def test_bracketing_is_reorder_invariant() -> None:
    """Shuffling the listed expiries does not change the selected set (replay determinism)."""
    listed = ["20260918", "20260619", "20291221", "20270618"]
    tenors = (3 / 12, 1.0, 3.0)
    forward = select_expiries_bracketing(listed, as_of=_AS_OF, tenor_years=tenors)
    reverse = select_expiries_bracketing(list(reversed(listed)), as_of=_AS_OF, tenor_years=tenors)
    assert forward == reverse == tuple(sorted(forward))


def test_bracketing_edge_cases() -> None:
    """Empty / garbage / single — each a labeled-or-empty outcome, never a crash."""
    tenors = (0.25, 1.0)
    assert select_expiries_bracketing([], as_of=_AS_OF, tenor_years=tenors) == ()
    # Unparseable tokens skipped, the one good token survives.
    assert select_expiries_bracketing(
        ["nonsense", "2026", "", "2026013x", "20260619"], as_of=_AS_OF, tenor_years=tenors
    ) == ("20260619",)
    assert select_expiries_bracketing(["20260619"], as_of=_AS_OF, tenor_years=tenors) == ("20260619",)


def test_chain_selection_targeting_flag_and_validation() -> None:
    """``targets_tenors`` needs both inputs; tenor_years are validated finite > 0."""
    assert not ChainSelection().targets_tenors                       # default: legacy
    assert not ChainSelection(tenor_years=(0.25,)).targets_tenors    # as_of missing → legacy
    assert ChainSelection(tenor_years=(0.25,), as_of=_AS_OF).targets_tenors
    with pytest.raises(ValueError):
        ChainSelection(tenor_years=(0.0,), as_of=_AS_OF)
    with pytest.raises(ValueError):
        ChainSelection(tenor_years=(float("nan"),), as_of=_AS_OF)


def test_plan_chain_brackets_when_targeted_else_nearest_n() -> None:
    """``plan_chain`` uses the bracket when targeted, and the untouched nearest-N otherwise."""
    chain = AvailableChain(
        exchange="SMART", trading_class="SPX", multiplier="100",
        expirations=("20260612", "20260619", "20260918", "20281215", "20291221"),
        strikes=(90.0, 100.0, 110.0),
    )
    targeted = plan_chain(
        "SPX", [chain], spot=100.0,
        selection=ChainSelection(tenor_years=(3 / 12, 3.0), as_of=_AS_OF, min_strikes_per_side=1),
    )
    assert targeted is not None
    # 3m≈2026-09-10 → {06-19, 09-18}; 3y≈2029-06-10 → {2028-12-15, 2029-12-21}.
    assert set(targeted.expiries) == {"20260619", "20260918", "20281215", "20291221"}
    legacy = plan_chain(
        "SPX", [chain], spot=100.0, selection=ChainSelection(max_expiries=2, min_strikes_per_side=1)
    )
    assert legacy is not None
    assert legacy.expiries == ("20260612", "20260619")  # nearest 2, unchanged


def _option(expiry: date) -> InstrumentKey:
    return InstrumentKey("SPX", "OPT", "CBOE", "USD", 100.0, f"c-{expiry}", expiry, 100.0, "C")


def test_select_capture_keys_brackets_the_streamed_set() -> None:
    """The *capture* stage (not just discovery) brackets too — it must not re-collapse to front.

    ``select_capture_keys`` bounded maturities by nearest-N before; targeted, it keeps the
    bracketing expiries so the streamed contracts span the term structure.
    """
    underlying = InstrumentKey("SPX", "IND", "CBOE", "USD", 1.0, "c-spx")
    options = [
        _option(date(2026, 6, 19)), _option(date(2026, 9, 18)), _option(date(2026, 12, 18)),
        _option(date(2027, 6, 18)), _option(date(2028, 12, 15)), _option(date(2029, 12, 21)),
    ]
    selection = ChainSelection(tenor_years=(3 / 12, 3.0), as_of=_AS_OF, min_strikes_per_side=1)
    captured = set(
        select_capture_keys(
            [underlying, *options], spots={"SPX": 100.0}, selection=selection, exchange="CBOE"
        )
    )
    kept = {key.expiry for key in options if key.canonical() in captured}
    # 3m brackets 06-19/09-18; 3y brackets 2028-12-15/2029-12-21; 12-18 and 2027-06-18 dropped.
    assert kept == {date(2026, 6, 19), date(2026, 9, 18), date(2028, 12, 15), date(2029, 12, 21)}
