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

from collections.abc import Callable, Mapping, Sequence

from contracts import InstrumentKey, InstrumentMaster, Position
from forwards import ForwardEstimate
from risk import ContractValuationInput
from snapshots import SnapshotBatch
from surfaces import SliceFit

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
    raise NotImplementedError(
        "actor valuation join — implemented by Workstream E wave-1 (S1) against this frozen seam"
    )
