"""No look-ahead in the close reference (roadmap WS 1C, Part B).

The close-snapshot mode resolves its spot through the reference-spot ladder's ``close`` rung
(``reference_spot.py`` line 74 spells out the look-ahead contract: ``prior_close`` MUST be a
value known at or before the snapshot instant). This test pins the contract directly: when there
is no live quote, the snapshot uses the supplied *prior* close — and a (would-be) *future* close
must never be the one that gets used. The independent oracle is the ladder's documented rung
order: with no two-sided quote and no last trade, the chosen reference is exactly the prior close
that was passed, byte-for-byte, never some later value.
"""

from __future__ import annotations

from datetime import UTC, datetime

from algotrading.infra.snapshots.reference_spot import resolve_reference_spot

CLOSE_INSTANT = datetime(2026, 3, 12, 20, 0, tzinfo=UTC)

PRIOR_CLOSE = 4321.0
FUTURE_CLOSE = 9999.0  # the value that must NEVER appear if we are point-in-time honest


def test_close_rung_uses_the_prior_close_when_no_live_quote() -> None:
    # No two-sided quote, no last trade: the ladder falls to the `close` rung and returns the
    # prior close. The future close is never passed in — the as-of-clean caller only ever has
    # the prior — and the resolved value is exactly that prior close.
    ref = resolve_reference_spot(bid=None, ask=None, last=None, prior_close=PRIOR_CLOSE)
    assert ref.reference_type == "close"
    assert ref.value == PRIOR_CLOSE
    assert ref.value != FUTURE_CLOSE


def test_a_live_two_sided_quote_beats_the_close_rung() -> None:
    # When a live close quote IS present it is used (mid), proving the close rung is a fallback,
    # not an override — the snapshot reflects the session's own close, not a stale prior.
    ref = resolve_reference_spot(bid=100.0, ask=100.2, last=None, prior_close=PRIOR_CLOSE)
    assert ref.reference_type == "mid"
    assert ref.value == 100.1


def test_feeding_only_a_future_close_is_indistinguishable_so_the_caller_must_not() -> None:
    # The resolver cannot detect a future value (it sees only the number) — which is exactly why
    # the contract puts the as-of guarantee on the caller. This test documents the trap: if a
    # caller wrongly passed FUTURE_CLOSE as prior_close, the resolver WOULD return it. The
    # live close path (orchestration.eod_stages) avoids this by sourcing the close from the
    # session's own events at the injected close instant, never a later one (see
    # test_cp_rest_close_capture.py's no-look-ahead drop).
    leaked = resolve_reference_spot(bid=None, ask=None, last=None, prior_close=FUTURE_CLOSE)
    assert leaked.value == FUTURE_CLOSE  # the resolver trusts the caller; the caller must be honest
