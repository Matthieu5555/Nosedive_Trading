from __future__ import annotations

from collections.abc import Mapping


class TermStructureError(KeyError):

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
    available = tuple(sorted(atm_vol_by_tenor))
    if front not in atm_vol_by_tenor:
        raise TermStructureError(front, available)
    if back not in atm_vol_by_tenor:
        raise TermStructureError(back, available)
    return atm_vol_by_tenor[back] - atm_vol_by_tenor[front]
