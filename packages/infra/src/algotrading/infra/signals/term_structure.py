"""Vol term-structure slope — the front-vs-back ATM-vol tilt (TARGET §3 S5 entry signal).

The sign of the ATM implied-vol term structure: positive when the back tenor prices a higher
vol than the front (contango / upward-sloping), negative in backwardation. It is the S5
calendar-carry entry trigger and a market-state diagnostic. Defined as a plain difference of
two named ATM-vol pillars so the unit is vol points and the sign is unambiguous:

    slope = sigma_atm(back) - sigma_atm(front).

Pure: a tenor→ATM-vol map plus the two pillar labels in, one scalar out.
"""

from __future__ import annotations

from collections.abc import Mapping


class TermStructureError(KeyError):
    """A requested term-slope pillar was absent from the ATM-vol map, carrying the label.

    The slope is undefined without both pillars; raised (rather than substituting a neighbour
    or a zero) so a missing tenor surfaces as the gap it is, never a fabricated flat slope.
    """

    def __init__(self, tenor_label: str, available: tuple[str, ...]) -> None:
        self.tenor_label = tenor_label
        self.available = available
        super().__init__(
            f"term-slope tenor {tenor_label!r} absent from ATM-vol map; available: {available}"
        )


def term_structure_slope(
    atm_vol_by_tenor: Mapping[str, float],
    *,
    front: str,
    back: str,
) -> float:
    """Back-minus-front ATM-vol slope from a tenor→ATM-vol map.

    ``front`` and ``back`` are the two pillar labels (e.g. ``"1m"`` and ``"3m"``); both must be
    present in ``atm_vol_by_tenor`` or :class:`TermStructureError` is raised naming the missing
    one. Returns ``sigma_atm(back) - sigma_atm(front)`` — positive in contango.
    """
    available = tuple(sorted(atm_vol_by_tenor))
    if front not in atm_vol_by_tenor:
        raise TermStructureError(front, available)
    if back not in atm_vol_by_tenor:
        raise TermStructureError(back, available)
    return atm_vol_by_tenor[back] - atm_vol_by_tenor[front]
