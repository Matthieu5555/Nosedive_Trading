from __future__ import annotations

import pytest
from algotrading.infra.signals import TermStructureError, term_structure_slope


def test_contango_is_positive() -> None:
    slope = term_structure_slope({"1m": 0.20, "3m": 0.23}, front="1m", back="3m")
    assert slope == pytest.approx(0.03)


def test_backwardation_is_negative() -> None:
    slope = term_structure_slope({"1m": 0.25, "3m": 0.20}, front="1m", back="3m")
    assert slope == pytest.approx(-0.05)


def test_flat_term_structure_is_zero() -> None:
    assert term_structure_slope({"1m": 0.21, "3m": 0.21}, front="1m", back="3m") == 0.0


@pytest.mark.parametrize("missing", ["1m", "6m"])
def test_missing_pillar_is_refused(missing: str) -> None:
    vols = {"1m": 0.20, "3m": 0.23}
    front, back = ("1m", "6m") if missing == "6m" else ("1m", "3m")
    available = {"1m", "3m"}
    if missing == "1m":
        vols = {"3m": 0.23, "6m": 0.24}
        front, back, available = "1m", "6m", {"3m", "6m"}
    with pytest.raises(TermStructureError) as excinfo:
        term_structure_slope(vols, front=front, back=back)
    assert excinfo.value.tenor_label == missing
    assert set(excinfo.value.available) == available
