"""Resolve one risk valuation input per held contract — the actor's join, math-free.

ADR 0006 decision 1 deferred exactly this to E: D's pure core takes one resolved
:class:`risk.ContractValuationInput` per contract and does *not* join C's snapshot,
forward and surface objects itself. That join is the actor's job, and it is pure
transport — it reads already-computed numbers off C's rich in-memory results and
copies them into the one typed input D prices from. It does no pricing math; the
only arithmetic it performs is the definitional change-of-variable between what C
publishes and what D's input names: log-moneyness ``k = ln(strike / forward)`` to
read the surface, the implied carry C already computed, and ``vol = sqrt(w / T)``
to turn the surface's total variance into the pricer's volatility. Everything else
is a field copy.

Inputs are the *rich* in-memory results from one actor run, not the persisted
contracts, because the persisted :class:`contracts.ForwardCurvePoint` drops the
discount factor and the persisted snapshot drops its QC verdict — both of which the
join needs. So the actor holds the :class:`forwards.ForwardEstimate`,
:class:`surfaces.SliceFit` and :class:`snapshots.SnapshotBatch` it just built and
joins from those.

Exercise style is not carried by any A contract (``InstrumentKey`` has no style
field), so it cannot be read from the universe. The actor takes a style *policy*
— a callable from instrument key to ``"european"``/``"american"`` — injected by the
caller, defaulting to European. That keeps the one unrepresented fact an explicit,
testable input rather than a guess buried in the join.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from contracts import InstrumentKey, InstrumentMaster, Position
from forwards import ForwardEstimate
from risk import CONFIDENCE_LOW, CONFIDENCE_OK, ContractValuationInput
from snapshots import SnapshotBatch
from surfaces import SliceFit

# A maturity is matched between a contract and its forward/slice when their
# ``maturity_years`` agree to this many decimals. The actor derives one
# ``maturity_years`` per (underlying, expiry) from the same day-count, so an exact
# float match is expected; the rounding only guards against a representation wobble
# between two computations of the same fraction.
_MATURITY_MATCH_DECIMALS = 9

# A non-dividend, non-future option exercised at expiry is the conservative default
# when no per-instrument style is known. Index options are European; single-name
# equity options are American — the caller injects that distinction via the policy.
DEFAULT_EXERCISE_STYLE = "european"


def default_exercise_style(instrument: InstrumentKey) -> str:
    """The fallback style policy: every contract is European unless overridden.

    Replace by injecting a policy that inspects ``instrument`` (e.g. American for
    ``security_type`` single-name equity options). Kept as a named function so the
    default is explicit and a test can pin it.
    """
    return DEFAULT_EXERCISE_STYLE


class ValuationJoinError(Exception):
    """A contract could not be resolved to a complete valuation input.

    Carries the offending ``contract_key`` and a plain-language reason (no usable
    snapshot for the underlying, no usable forward for the maturity, no fitted
    slice, missing instrument master), so a failed join names the contract and the
    missing piece instead of producing a silent ``NaN`` or dropping the line.
    """

    def __init__(self, contract_key: str, reason: str) -> None:
        self.contract_key = contract_key
        self.reason = reason
        super().__init__(f"valuation join for {contract_key!r}: {reason}")


def resolve_valuation_inputs(
    positions: Sequence[Position],
    *,
    snapshots: SnapshotBatch,
    forwards: Sequence[ForwardEstimate],
    slices: Sequence[SliceFit],
    masters: Mapping[str, InstrumentMaster],
    exercise_style_for: Callable[[InstrumentKey], str] = default_exercise_style,
) -> dict[str, ContractValuationInput]:
    """Build one :class:`ContractValuationInput` per distinct held contract.

    Returns a mapping keyed by ``contract_key`` (deduplicated across lots — two
    lots of one contract share one market state, which is also what D's
    ``net_lots`` requires). The resolution for each contract, all field-copy except
    the three definitional conversions noted in the module docstring:

    * **identity / monetization** — ``underlying``, ``option_right``, ``strike``,
      ``multiplier``, ``currency`` come from the contract's
      :class:`contracts.InstrumentMaster` (its :class:`InstrumentKey`); a missing
      master raises :class:`ValuationJoinError`.
    * **exercise_style** — ``exercise_style_for(instrument)``.
    * **spot** — the underlying's usable :class:`contracts.MarketStateSnapshot`
      ``reference_spot``; no usable underlying snapshot raises.
    * **forward / carry / discount_factor / maturity_years** — the usable
      :class:`forwards.ForwardEstimate` for ``(underlying, maturity)``: its
      ``forward``, ``implied_carry``, ``discount_factor`` and ``maturity_years``.
      A maturity with no usable forward raises rather than guessing a carry.
    * **volatility** — the fitted :class:`surfaces.SliceFit` for the maturity,
      read at ``k = ln(strike / forward)`` via ``slice.total_variance(k)``, then
      ``vol = sqrt(w / maturity_years)``. No fitted slice raises.
    * **confidence** — ``CONFIDENCE_OK`` when the contract's own snapshot QC verdict
      is usable, else ``CONFIDENCE_LOW``, so a low-quality quote is priced and
      labeled (D's convention) rather than dropped.

    The function reads only published numbers and copies them; it must not price,
    bump, or re-fit anything. A contract that cannot be completed is surfaced as a
    :class:`ValuationJoinError` naming the missing piece — never silently skipped.
    """
    spot_by_underlying = _usable_spot_by_underlying(snapshots)
    verdict_usable_by_key = _verdict_usable_by_contract(snapshots)
    forward_by_key = _forward_by_underlying_maturity(forwards)
    slices_by_underlying_expiry = _index_slices_by_expiry(slices)

    resolved: dict[str, ContractValuationInput] = {}
    for position in positions:
        contract_key = position.contract_key
        if contract_key in resolved:
            # Dedup by contract across lots — two lots share one market state.
            continue

        master = masters.get(contract_key)
        if master is None:
            raise ValuationJoinError(contract_key, "no instrument master")
        instrument = master.instrument

        underlying = instrument.underlying_symbol
        spot = spot_by_underlying.get(underlying)
        if spot is None:
            raise ValuationJoinError(
                contract_key, f"no usable snapshot for underlying {underlying!r}"
            )

        slice_fit = _slice_for_contract(
            contract_key, underlying, instrument.expiry, slices_by_underlying_expiry
        )
        maturity_years = slice_fit.maturity_years

        forward_estimate = forward_by_key.get(
            (underlying, round(maturity_years, _MATURITY_MATCH_DECIMALS))
        )
        if forward_estimate is None:
            raise ValuationJoinError(
                contract_key,
                f"no usable forward for ({underlying!r}, maturity {maturity_years:g})",
            )

        strike = instrument.strike
        if strike is None:
            raise ValuationJoinError(contract_key, "contract has no strike")
        option_right = instrument.option_right
        if option_right is None:
            raise ValuationJoinError(contract_key, "contract has no option right")

        forward = forward_estimate.forward
        carry = forward_estimate.implied_carry
        discount_factor = forward_estimate.discount_factor
        if forward is None or discount_factor is None or carry is None:
            raise ValuationJoinError(
                contract_key,
                f"forward for ({underlying!r}, maturity {maturity_years:g}) is incomplete",
            )

        # The only arithmetic: the surface read coordinate and the variance->vol
        # change of variable. Both are definitional, not pricing.
        log_moneyness = math.log(strike / forward)
        total_variance = slice_fit.total_variance(log_moneyness)
        volatility = math.sqrt(total_variance / maturity_years)

        confidence = (
            CONFIDENCE_OK if verdict_usable_by_key.get(contract_key, False) else CONFIDENCE_LOW
        )

        resolved[contract_key] = ContractValuationInput(
            contract_key=contract_key,
            underlying=underlying,
            option_right=option_right,
            exercise_style=exercise_style_for(instrument),
            strike=strike,
            maturity_years=maturity_years,
            spot=spot,
            carry=carry,
            volatility=volatility,
            discount_factor=discount_factor,
            multiplier=instrument.multiplier,
            currency=instrument.currency,
            confidence=confidence,
        )
    return resolved


def _usable_spot_by_underlying(snapshots: SnapshotBatch) -> dict[str, float]:
    """Map each underlying symbol to the reference spot of its usable snapshot.

    Only a snapshot whose own QC verdict is usable supplies a spot; a rejected
    underlying quote is not an honest spot to price against.
    """
    spot_by_underlying: dict[str, float] = {}
    for assessed in snapshots.assessed:
        if not assessed.assessment.is_usable:
            continue
        snapshot = assessed.snapshot
        # An underlying snapshot's instrument_key equals its underlying-keyed self;
        # an option snapshot also carries the underlying symbol but is not the spot.
        if snapshot.instrument_key == _underlying_key_of(snapshot.instrument_key):
            spot_by_underlying.setdefault(snapshot.underlying, snapshot.reference_spot)
    return spot_by_underlying


def _underlying_key_of(instrument_key: str) -> str:
    """The canonical key with the option-only slots blank — its underlying's key.

    An :class:`InstrumentKey`'s canonical string ends in three slots
    (``expiry|strike|right``) that are empty for an underlying. A snapshot is an
    underlying's snapshot exactly when those three trailing slots are empty.
    """
    fields = instrument_key.split("|")
    if len(fields) != 9:
        return instrument_key
    fields[6] = ""
    fields[7] = ""
    fields[8] = ""
    return "|".join(fields)


def _verdict_usable_by_contract(snapshots: SnapshotBatch) -> dict[str, bool]:
    """Map each snapshot's contract key to whether its QC verdict is usable."""
    return {
        assessed.snapshot.instrument_key: assessed.assessment.is_usable
        for assessed in snapshots.assessed
    }


def _forward_by_underlying_maturity(
    forwards: Sequence[ForwardEstimate],
) -> dict[tuple[str, float], ForwardEstimate]:
    """Index usable forward estimates by ``(underlying, rounded maturity_years)``."""
    index: dict[tuple[str, float], ForwardEstimate] = {}
    for estimate in forwards:
        if not estimate.is_usable:
            continue
        key = (estimate.underlying, round(estimate.maturity_years, _MATURITY_MATCH_DECIMALS))
        index.setdefault(key, estimate)
    return index


def _index_slices_by_expiry(
    slices: Sequence[SliceFit],
) -> dict[tuple[str, object], SliceFit]:
    """Index fitted (non-insufficient) slices by ``(underlying, expiry)``."""
    by_expiry: dict[tuple[str, object], SliceFit] = {}
    for slice_fit in slices:
        if not _slice_has_curve(slice_fit):
            continue
        by_expiry.setdefault((slice_fit.underlying, slice_fit.expiry_date), slice_fit)
    return by_expiry


def _slice_has_curve(slice_fit: SliceFit) -> bool:
    """True when the slice fit a curve (svi or nonparametric), not ``insufficient``."""
    return slice_fit.svi is not None or bool(slice_fit.nonparametric_ks)


def _slice_for_contract(
    contract_key: str,
    underlying: str,
    expiry: object,
    slices_by_underlying_expiry: Mapping[tuple[str, object], SliceFit],
) -> SliceFit:
    """Find the fitted slice for a contract by (underlying, expiry), or raise."""
    if expiry is None:
        raise ValuationJoinError(contract_key, "contract has no expiry")
    slice_fit = slices_by_underlying_expiry.get((underlying, expiry))
    if slice_fit is None:
        raise ValuationJoinError(
            contract_key, f"no fitted slice for ({underlying!r}, expiry {expiry})"
        )
    return slice_fit
