"""WS 1B — direct unit tests for the delta-band chain-selection policy.

The README flagged a direct ``chain_planning`` test as the coverage gap to close before
the delta-band variant lands; this is that test. It pins :func:`select_strikes_delta_band`
against independently-derived expected boundaries and exercises the named cases the 1B
spec's Test surface enumerates, plus the TESTING.md edge-case floor.

Independent oracle (TESTING.md "never test code against itself"): the 30Δ put/call
boundary strikes are computed by inverting the standard-normal CDF with
``scipy.stats.norm.ppf`` (a *different* implementation from the pricing engine's
``math.erf`` path) in :func:`fixtures.synthetic.delta_band_boundary_strike`. The selection
code reads its delta from the Black-76 engine; the expected boundary is computed from
``norm.ppf`` — two independent code paths, so a passing assertion is real agreement, not a
round-trip. The boundary is mildly vol-dependent, so every expected set is derived at the
*same* working vol the selection code is handed.

The %-of-spot ``select_strikes`` is unchanged; its behaviour stays covered indirectly by
``test_collection_use_cases.py`` / ``test_orchestration.py``. A small regression assertion
here pins that the two policies are distinct functions over the same input shape.
"""

from __future__ import annotations

import math

import pytest
from algotrading.core.config import StrikeSelectionConfig, load_platform_config
from algotrading.infra.universe import (
    StrikeSelectionError,
    select_strikes,
    select_strikes_delta_band,
)
from fixtures.synthetic import build_delta_band_ladder, delta_band_boundary_strike


def _cfg(
    *,
    delta_bound: float = 0.30,
    delta_convention: str = "forward_undiscounted",
    min_strikes_per_side: int = 1,
) -> StrikeSelectionConfig:
    """A delta-band config for tests. ``min_strikes_per_side`` defaults to 1 so the floor
    does not mask the band itself except where a case is explicitly about the floor."""
    return StrikeSelectionConfig(
        version="strike-selection-test",
        delta_bound=delta_bound,
        delta_convention=delta_convention,
        min_strikes_per_side=min_strikes_per_side,
    )


def test_delta_band_spans_30d_put_to_30d_call() -> None:
    """The selected set is exactly the contiguous block of listed strikes in [30Δ put, 30Δ call].

    Expected boundaries are derived independently (scipy ``norm.ppf``), not by calling the
    band function. The ladder carries interior strikes inside the band, wings outside it, and
    the two exact boundary strikes; the expected band is the listed strikes within the oracle
    boundaries, computed by the fixture without the selection code.
    """
    ladder = build_delta_band_ladder()
    expected = ladder.expected_band()
    got = select_strikes_delta_band(
        ladder.strikes,
        forward=ladder.forward,
        maturity_years=ladder.maturity_years,
        discount_factor=ladder.discount_factor,
        volatility=ladder.volatility,
        selection=_cfg(),
    )
    assert got == expected
    # Sanity on the oracle itself: the band is the central block, wings are dropped.
    assert min(got) >= ladder.put_boundary - 1e-9 * ladder.forward
    assert max(got) <= ladder.call_boundary + 1e-9 * ladder.forward
    assert 80.0 not in got and 120.0 not in got  # wings excluded


def test_count_varies_with_listing_density() -> None:
    """WS 1B acceptance: a dense ladder yields strictly more strikes than a sparse one.

    Both selections run over the *same* delta window (same forward/vol/T/DF and config); only
    the listing density differs. Both must lie inside the band, and the dense count must
    strictly exceed the sparse count — the property that only makes sense for the contiguous
    block, not three pillars.
    """
    forward, maturity, vol, df = 100.0, 0.25, 0.20, 0.99
    put_b = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.70
    )
    call_b = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.30
    )
    # Dense: a 1-point grid across the band. Sparse: every 5th point of it.
    dense = tuple(float(k) for k in range(int(put_b) - 2, int(call_b) + 3))
    sparse = dense[::5]
    selection = _cfg()
    dense_got = select_strikes_delta_band(
        dense, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=selection,
    )
    sparse_got = select_strikes_delta_band(
        sparse, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=selection,
    )
    assert len(dense_got) > len(sparse_got)
    tol = 1e-9 * forward
    for kept in (*dense_got, *sparse_got):
        assert put_b - tol <= kept <= call_b + tol


def test_band_is_per_tenor() -> None:
    """The same listed strike is kept at one tenor and dropped at another.

    A strike near the 30Δ call boundary at a near tenor moves deeper out of the band at a far
    tenor (the call delta of a fixed strike falls as maturity lengthens at this forward), so
    selection over the *same* strike list differs by tenor — proving the band is recomputed
    per expiry, not once on a representative tenor.
    """
    forward, vol, df = 100.0, 0.20, 0.99
    strikes = tuple(float(k) for k in range(80, 121))
    near = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=0.10, discount_factor=df,
        volatility=vol, selection=_cfg(),
    )
    far = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=2.00, discount_factor=df,
        volatility=vol, selection=_cfg(),
    )
    assert near != far
    # The far-tenor band is wider in strike space (more total variance), so its OTM-call edge
    # extends to higher strikes than the near tenor's.
    assert max(far) > max(near)


def test_delta_sign_and_atm_included() -> None:
    """ATM is always inside the band; a 10Δ wing is excluded; the 30Δ-exact strike is kept.

    The boundary-exact case: a strike placed by the oracle at exactly N(d1)=0.30 (the 30Δ
    call) sits *on* the boundary and is kept (the comparison is inclusive). A 10Δ wing
    (|delta| ≈ 0.10, far outside 0.30) is excluded.
    """
    forward, maturity, vol, df = 100.0, 0.25, 0.20, 0.99
    atm = forward
    call_30 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.30
    )
    put_30 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.70
    )
    call_10 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.10
    )
    put_10 = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.90
    )
    strikes = (put_10, put_30, atm, call_30, call_10)
    got = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=_cfg(),
    )
    assert atm in got  # ATM (|delta| ~ 0.5) always inside
    assert call_30 in got and put_30 in got  # 30Δ-exact strikes kept (boundary inclusive)
    assert call_10 not in got and put_10 not in got  # 10Δ wings excluded


def test_convention_pinned() -> None:
    """Flipping the convention flag moves the boundary; a bad flag raises ConfigFieldError.

    With the same listed strikes, the discounted convention (DF·N(d1)) reaches the 0.30 bound
    at a *different* strike than the undiscounted convention (N(d1)): a strike whose
    undiscounted call delta is just above 0.30 can fall below 0.30 once multiplied by DF, so
    it is kept under ``forward_undiscounted`` but dropped under ``spot_discounted``. A bad
    convention value raises a labeled ConfigFieldError (ADR 0028), never a silent default.
    """
    from algotrading.core.config import ConfigFieldError

    forward, maturity, vol = 100.0, 0.25, 0.20
    df = 0.90  # an exaggerated discount so the two conventions split visibly
    # The strike whose UNDISCOUNTED call delta is exactly 0.30 (kept undiscounted, on-boundary).
    edge = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.30
    )
    # A strike comfortably inside the DISCOUNTED band above the forward (so the above side has a
    # band strike and the per-side floor does not re-add `edge` and mask the convention split).
    inside_above = delta_band_boundary_strike(
        forward=forward, maturity_years=maturity, volatility=vol, target_call_nd1=0.45
    )
    strikes = (forward, inside_above, edge)
    undiscounted = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=_cfg(delta_convention="forward_undiscounted"),
    )
    discounted = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=_cfg(delta_convention="spot_discounted"),
    )
    assert edge in undiscounted  # N(d1)=0.30 == bound, inclusive
    # DF*N(d1) = 0.90*0.30 = 0.27 < 0.30, dropped under the discounted flag; the floor does not
    # re-add it because `inside_above` already satisfies the above-side floor.
    assert edge not in discounted

    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(
            version="bad", delta_bound=0.30, delta_convention="nonsense",
            min_strikes_per_side=1,
        )


def test_strike_selection_config_validation() -> None:
    """The typed config rejects out-of-range economic fields with a labeled error."""
    from algotrading.core.config import ConfigFieldError

    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(version="", delta_bound=0.30)  # empty version
    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(version="v", delta_bound=0.0)  # bound must be in (0,1)
    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(version="v", delta_bound=1.0)  # a call delta is in [0,1]
    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(version="v", delta_bound=float("nan"))  # non-finite
    with pytest.raises(ConfigFieldError):
        StrikeSelectionConfig(version="v", delta_bound=0.30, min_strikes_per_side=0)


def test_shipped_universe_config_carries_the_band() -> None:
    """The shipped configs/universe.yaml builds the delta band through the typed from_config path.

    Proves the YAML↔dataclass seam (ADR 0028): the band is loaded as a typed
    ``StrikeSelectionConfig``, not a ``.py`` literal, and is usable directly for selection.
    """
    config = load_platform_config("configs")
    selection = config.universe.strike_selection
    assert isinstance(selection, StrikeSelectionConfig)
    assert selection.delta_bound == 0.30
    assert selection.delta_convention == "forward_undiscounted"
    ladder = build_delta_band_ladder()
    got = select_strikes_delta_band(
        ladder.strikes,
        forward=ladder.forward,
        maturity_years=ladder.maturity_years,
        discount_factor=ladder.discount_factor,
        volatility=ladder.volatility,
        selection=selection,
    )
    assert got  # a non-empty band over a sane ladder


# --- TESTING.md edge-case floor -------------------------------------------------------------


def test_empty_strike_list_returns_empty() -> None:
    """Empty strike list → ``()`` (degenerate shape), not a crash or a bare NaN."""
    got = select_strikes_delta_band(
        (), forward=100.0, maturity_years=0.25, discount_factor=0.99,
        volatility=0.20, selection=_cfg(),
    )
    assert got == ()


def test_single_strike_returns_that_strike() -> None:
    """A single listed strike is returned (the floor keeps the lone nearest-the-money strike)."""
    got = select_strikes_delta_band(
        (100.0,), forward=100.0, maturity_years=0.25, discount_factor=0.99,
        volatility=0.20, selection=_cfg(min_strikes_per_side=1),
    )
    assert got == (100.0,)


def test_all_wing_ladder_falls_back_to_floor() -> None:
    """A ladder with nothing inside 30Δ returns the nearest-the-money floor, labeled, not empty.

    Every strike is a deep wing (|delta| far below 0.30), so the band is empty; the per-side
    floor then returns the ``min_strikes_per_side`` nearest-the-money strikes each side of the
    forward — a deterministic, labeled fallback (documented on the function), never a silent
    empty result.
    """
    forward = 100.0
    wings = (40.0, 50.0, 60.0, 160.0, 170.0, 180.0)  # all far OTM either side
    got = select_strikes_delta_band(
        wings, forward=forward, maturity_years=0.25, discount_factor=0.99,
        volatility=0.20, selection=_cfg(min_strikes_per_side=2),
    )
    # 2 nearest below (60, 50) and 2 nearest above (160, 170) the forward.
    assert got == (50.0, 60.0, 160.0, 170.0)


def test_boundary_exact_min_strikes_floor_when_band_thin() -> None:
    """When the band yields fewer than the per-side floor, the floor fills that side only.

    A ladder where the band naturally holds one strike above the forward but the floor needs
    two: the below side is left as the band found it, the above side is filled to the floor —
    the per-side floor matching the %-of-spot policy, not a global override.
    """
    forward, maturity, vol, df = 100.0, 0.25, 0.20, 0.99
    # Interior strikes only below+at the money; a single just-inside strike above.
    strikes = (94.0, 96.0, 98.0, 100.0, 104.0, 130.0)
    got = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=maturity, discount_factor=df,
        volatility=vol, selection=_cfg(min_strikes_per_side=2),
    )
    # Above side band had {104} (1 < 2) → floor fills above_all[:2] = {104, 130}.
    assert 104.0 in got and 130.0 in got
    # Below side band intact (multiple strikes inside), 130 only present via the floor fill.
    assert 100.0 in got and 98.0 in got


@pytest.mark.parametrize(
    ("field", "kwargs"),
    [
        ("forward", {"forward": 0.0}),
        ("forward", {"forward": float("nan")}),
        ("forward", {"forward": -100.0}),
        ("volatility", {"volatility": 0.0}),
        ("volatility", {"volatility": float("nan")}),
        ("volatility", {"volatility": float("inf")}),
        ("maturity_years", {"maturity_years": 0.0}),
        ("maturity_years", {"maturity_years": -0.25}),
        ("discount_factor", {"discount_factor": 0.0}),
        ("discount_factor", {"discount_factor": 1.5}),
        ("discount_factor", {"discount_factor": float("nan")}),
    ],
)
def test_unusable_pricing_input_raises_labeled_error(field: str, kwargs: dict) -> None:
    """A missing/zero/non-finite pricing input raises a *labeled* StrikeSelectionError.

    Never a bare NaN strike silently entering the chain, never a crash deep in the pricer —
    the TESTING.md negative-path floor. The error names the offending field.
    """
    base = dict(
        forward=100.0, maturity_years=0.25, discount_factor=0.99, volatility=0.20,
    )
    base.update(kwargs)
    with pytest.raises(StrikeSelectionError) as exc:
        select_strikes_delta_band((90.0, 100.0, 110.0), selection=_cfg(), **base)
    assert exc.value.field == field


def test_kept_strikes_are_sorted_deduped_and_finite() -> None:
    """Output is ascending, de-duplicated, and finite — never a bare NaN strike."""
    forward = 100.0
    strikes = (100.0, 100.0, 98.0, 102.0, 98.0)  # duplicates present
    got = select_strikes_delta_band(
        strikes, forward=forward, maturity_years=0.25, discount_factor=0.99,
        volatility=0.20, selection=_cfg(),
    )
    assert list(got) == sorted(got)
    assert len(got) == len(set(got))
    assert all(math.isfinite(k) for k in got)


def test_reordering_input_does_not_change_band() -> None:
    """Shuffling the listed strikes does not change the selected set (reordering invariance)."""
    ladder = build_delta_band_ladder()
    forward_order = select_strikes_delta_band(
        ladder.strikes, forward=ladder.forward, maturity_years=ladder.maturity_years,
        discount_factor=ladder.discount_factor, volatility=ladder.volatility, selection=_cfg(),
    )
    reverse_order = select_strikes_delta_band(
        tuple(reversed(ladder.strikes)), forward=ladder.forward,
        maturity_years=ladder.maturity_years, discount_factor=ladder.discount_factor,
        volatility=ladder.volatility, selection=_cfg(),
    )
    assert forward_order == reverse_order


def test_percent_of_spot_policy_is_distinct_and_unchanged() -> None:
    """Regression guard: the %-of-spot select_strikes is a separate function over the same shape.

    The two policies coexist; selecting the same ladder by %-of-spot vs delta-band yields
    different sets (the delta band is tighter than a 35% window here), proving the delta band
    did not replace or alter the %-of-spot policy.
    """
    from algotrading.infra.universe import ChainSelection

    strikes = tuple(float(k) for k in range(80, 121, 2))
    pct = select_strikes(strikes, 100.0, ChainSelection(min_strikes_per_side=1))
    band = select_strikes_delta_band(
        strikes, forward=100.0, maturity_years=0.25, discount_factor=0.99,
        volatility=0.20, selection=_cfg(),
    )
    assert pct != band
    assert set(band).issubset(set(strikes))
    assert set(pct).issubset(set(strikes))
