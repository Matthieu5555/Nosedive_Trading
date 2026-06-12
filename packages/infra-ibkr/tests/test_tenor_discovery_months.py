"""T-tenor-selection — the IBKR month-token side of tenor-targeted discovery.

CP ``secdef/search`` lists option months as ``MMMYY`` tokens (``JUN26;…;DEC31``); one token
deflates to several concrete expiries via ``secdef/info``. Discovery used to qualify the first
``max_expiries`` tokens (a front-loaded slice that never reached the long end). These tests pin the
replacement: qualify the tokens **straddling each pinned tenor's target date**, so the long-dated
expiries (2y/3y — the broker lists SPX to DEC2031, SX5E to DEC2035) are actually discovered.

Independent oracle: the expected token sets are hand-derived from the tokens' mid-month dates and
hand-computed tenor targets, not read back from the function under test.
"""

from __future__ import annotations

from datetime import date

from algotrading.infra.surfaces import tenor_years
from algotrading.infra.universe import ChainSelection
from algotrading.infra_ibkr.collectors.cp_rest_close_capture import (
    _parse_month_token,
    _select_discovery_months,
)

_AS_OF = date(2026, 6, 11)
_GRID = ("10d", "1m", "3m", "6m", "12m", "18m", "2y", "3y")


def _targeted_selection() -> ChainSelection:
    return ChainSelection(
        tenor_years=tuple(tenor_years(label) for label in _GRID), as_of=_AS_OF
    )


def test_parse_month_token() -> None:
    """``MMMYY`` → a mid-month representative date; junk → ``None`` (skipped, never guessed)."""
    assert _parse_month_token("DEC28") == date(2028, 12, 15)
    assert _parse_month_token("jun26") == date(2026, 6, 15)  # case-insensitive
    assert _parse_month_token("XXX99") is None               # bad month
    assert _parse_month_token("JUN261") is None              # wrong length
    assert _parse_month_token("JUN2") is None
    assert _parse_month_token("") is None


def test_discovery_months_bracket_to_the_long_end() -> None:
    """Targeted discovery qualifies the tokens straddling each tenor, reaching 3y — not just front.

    SPX-shaped token list out to DEC2031. The grid's 3y target (≈2029-06-10) brackets DEC28/DEC29,
    so DEC29 is qualified and the longer DEC30/DEC31 — past every tenor — are not.
    """
    months = (
        "JUN26", "JUL26", "SEP26", "DEC26", "JUN27", "SEP27", "DEC27",
        "DEC28", "DEC29", "DEC30", "DEC31",
    )
    got = _select_discovery_months(months, _targeted_selection())
    assert got == (
        "JUN26", "JUL26", "SEP26", "DEC26", "JUN27", "SEP27", "DEC27", "DEC28", "DEC29",
    )
    assert "DEC30" not in got and "DEC31" not in got  # past the 3y bracket, not qualified


def test_discovery_months_legacy_slice_when_untargeted() -> None:
    """With no tenor targeting, the old nearest-N token slice is preserved verbatim."""
    months = ("JUN26", "JUL26", "SEP26", "DEC26", "MAR27")
    got = _select_discovery_months(months, ChainSelection(max_expiries=3))
    assert got == ("JUN26", "JUL26", "SEP26")


def test_discovery_months_unparseable_falls_back_to_legacy() -> None:
    """If no token parses (a wire-shape surprise), degrade to the legacy slice, not an empty chain."""
    months = ("garbage", "still-bad")
    got = _select_discovery_months(months, _targeted_selection())
    assert got == ("garbage", "still-bad")  # max_expiries == len(grid) == 8, so all kept
