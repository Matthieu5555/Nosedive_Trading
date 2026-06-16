from __future__ import annotations

from datetime import UTC, datetime

from algotrading.infra.snapshots.reference_spot import resolve_reference_spot

CLOSE_INSTANT = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)

PRIOR_CLOSE = 4321.0
FUTURE_CLOSE = 9999.0


def test_close_rung_uses_the_prior_close_when_no_live_quote() -> None:
    ref = resolve_reference_spot(bid=None, ask=None, last=None, prior_close=PRIOR_CLOSE)
    assert ref.reference_type == "close"
    assert ref.value == PRIOR_CLOSE
    assert ref.value != FUTURE_CLOSE


def test_a_live_two_sided_quote_beats_the_close_rung() -> None:
    ref = resolve_reference_spot(bid=100.0, ask=100.2, last=None, prior_close=PRIOR_CLOSE)
    assert ref.reference_type == "mid"
    assert ref.value == 100.1


def test_feeding_only_a_future_close_is_indistinguishable_so_the_caller_must_not() -> None:
    leaked = resolve_reference_spot(bid=None, ask=None, last=None, prior_close=FUTURE_CLOSE)
    assert leaked.value == FUTURE_CLOSE
