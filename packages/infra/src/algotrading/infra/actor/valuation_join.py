from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence

from algotrading.infra.contracts import InstrumentKey, InstrumentMaster, Position
from algotrading.infra.forwards import ForwardEstimate
from algotrading.infra.risk import CONFIDENCE_LOW, CONFIDENCE_OK, ContractValuationInput
from algotrading.infra.snapshots import SnapshotBatch
from algotrading.infra.surfaces import SliceFit

_MATURITY_MATCH_DECIMALS = 9

DEFAULT_EXERCISE_STYLE = "european"


def default_exercise_style(instrument: InstrumentKey) -> str:
    return DEFAULT_EXERCISE_STYLE


class ValuationJoinError(Exception):

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
    spot_by_underlying = _usable_spot_by_underlying(snapshots)
    verdict_usable_by_key = _verdict_usable_by_contract(snapshots)
    forward_by_key = _forward_by_underlying_maturity(forwards)
    slices_by_underlying_expiry = _index_slices_by_expiry(slices)

    resolved: dict[str, ContractValuationInput] = {}
    for position in positions:
        contract_key = position.contract_key
        if contract_key in resolved:
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
    spot_by_underlying: dict[str, float] = {}
    for assessed in snapshots.assessed:
        if not assessed.assessment.is_usable:
            continue
        snapshot = assessed.snapshot
        if snapshot.instrument_key == _underlying_key_of(snapshot.instrument_key):
            spot_by_underlying.setdefault(snapshot.underlying, snapshot.reference_spot)
    return spot_by_underlying


def _underlying_key_of(instrument_key: str) -> str:
    fields = instrument_key.split("|")
    if len(fields) != 9:
        return instrument_key
    fields[6] = ""
    fields[7] = ""
    fields[8] = ""
    return "|".join(fields)


def _verdict_usable_by_contract(snapshots: SnapshotBatch) -> dict[str, bool]:
    return {
        assessed.snapshot.instrument_key: assessed.assessment.is_usable
        for assessed in snapshots.assessed
    }


def _forward_by_underlying_maturity(
    forwards: Sequence[ForwardEstimate],
) -> dict[tuple[str, float], ForwardEstimate]:
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
    by_expiry: dict[tuple[str, object], SliceFit] = {}
    for slice_fit in slices:
        if not _slice_has_curve(slice_fit):
            continue
        by_expiry.setdefault((slice_fit.underlying, slice_fit.expiry_date), slice_fit)
    return by_expiry


def _slice_has_curve(slice_fit: SliceFit) -> bool:
    return slice_fit.svi is not None or bool(slice_fit.nonparametric_ks)


def _slice_for_contract(
    contract_key: str,
    underlying: str,
    expiry: object,
    slices_by_underlying_expiry: Mapping[tuple[str, object], SliceFit],
) -> SliceFit:
    if expiry is None:
        raise ValuationJoinError(contract_key, "contract has no expiry")
    slice_fit = slices_by_underlying_expiry.get((underlying, expiry))
    if slice_fit is None:
        raise ValuationJoinError(
            contract_key, f"no fitted slice for ({underlying!r}, expiry {expiry})"
        )
    return slice_fit
