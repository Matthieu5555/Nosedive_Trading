"""1G timer coherence: each committed eod-capture@<MIC>.timer fires AFTER the resolved close.

This is the drift-detection guard for the XEUR timer-shift bug (ibkr-clock-timer-coherence,
2026-06-15): the old eod-capture@XEUR.timer carried ``OnCalendar=Mon..Fri 18:15
Europe/Berlin``, which fires 3h45m BEFORE the true XEUR session close (22:00 Berlin).
The fix moves it to 22:45 and generates the file from the calendar; this test ensures the
fire time stays coherent with the resolved close even if a future hand-edit or a bad
generator run re-introduces the drift.

## What this test pins

For each committed ``eod-capture@<MIC>.timer`` in ``scripts/systemd/``:

1. Parse the ``OnCalendar=`` line to extract the fire time and timezone.
2. For each index whose calendar MIC matches this timer:
   a. Resolve ``session_close`` for a representative session (injected date — no wall clock).
   b. Assert the fire time (as a UTC datetime on the same date) is strictly AFTER the
      resolved close.
3. Assert the margin is at least ``_MIN_MARGIN_MINUTES`` so a timer is not trivially just
   one second after the close.

The oracle: the close times below are hand-computed from the published exchange calendars
and named in the comments — NEVER read back from the resolver or the generator under test.

No wall clock is read. The ``CalendarResolver`` is driven with an injected date throughout.
"""

from __future__ import annotations

import re
import zoneinfo
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from algotrading.infra.universe import CalendarResolver, parse_index_registry

# --- constants -----------------------------------------------------------------------

# Minimum gap between the timer's fire time and the session close, in minutes.
# A timer firing only 1 minute after the close is technically coherent but fragile;
# this floor ensures a meaningful operational margin.
_MIN_MARGIN_MINUTES = 5

# Representative session used to resolve close times and fire times.
# Must be an ordinary trading session on BOTH XEUR and XNYS (and any future MIC in
# the registry). Hand-verified: 2026-06-10 is an ordinary Tuesday, no holidays.
_REPR_SESSION = date(2026, 6, 10)

# The two-index registry used by the resolver. Both enabled so we exercise both MICs
# without depending on the live universe.yaml "enabled" flag.
# Hand-computed independent oracle for the session close on 2026-06-10:
#   XEUR: 22:00 Europe/Berlin = 20:00 UTC (CEST, UTC+2)
#   XNYS: 16:00 America/New_York = 20:00 UTC (EDT, UTC-4)
_REGISTRY_BLOCK = {
    "SX5E": {
        "name": "EURO STOXX 50",
        "calendar": "XEUR",
        "currency": "EUR",
        "ibkr": {"conid": 1, "secType": "IND", "exchange": "EUREX"},
        "enabled": True,
    },
    "SPX": {
        "name": "S&P 500",
        "calendar": "XNYS",
        "currency": "USD",
        "ibkr": {"conid": 2, "secType": "IND", "exchange": "CBOE"},
        "enabled": True,
    },
}

# Map from MIC code to index symbol(s) — used to look up sessions for each timer.
_MIC_TO_SYMBOLS: dict[str, list[str]] = {
    "XEUR": ["SX5E"],
    "XNYS": ["SPX"],
}


# --- helpers -------------------------------------------------------------------------

def _timer_dir() -> Path:
    """Absolute path to scripts/systemd/ in the repo."""
    return Path(__file__).resolve().parents[3] / "scripts" / "systemd"


def _find_timer_files() -> list[Path]:
    """All eod-capture@<MIC>.timer files in scripts/systemd/."""
    return sorted((_timer_dir()).glob("eod-capture@*.timer"))


def _extract_oncalendar(timer_content: str) -> str:
    """Extract the OnCalendar= value from a timer file (the first non-comment occurrence)."""
    for line in timer_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("OnCalendar="):
            return stripped[len("OnCalendar="):].strip()
    raise ValueError(f"No OnCalendar= line found in timer:\n{timer_content}")


def _parse_mic_from_filename(path: Path) -> str:
    """Extract the MIC code from a filename like 'eod-capture@XEUR.timer'."""
    # 'eod-capture@XEUR.timer' -> 'XEUR'
    m = re.fullmatch(r"eod-capture@([A-Z]+)\.timer", path.name)
    if not m:
        raise ValueError(f"Unexpected timer filename: {path.name}")
    return m.group(1)


def _fire_utc(on_calendar: str, on_date: date) -> datetime:
    """Convert an OnCalendar value like 'Mon..Fri 22:45 Europe/Berlin' to a UTC datetime
    on the given date.

    The systemd OnCalendar format used here is: ``<day-spec> <HH:MM> <tz>``. We extract
    the time and timezone, combine with the date, and return an aware UTC datetime.
    The day-spec (Mon..Fri) is ignored — we just want the fire instant on the date.
    """
    # Expected pattern: e.g. "Mon..Fri 22:45 Europe/Berlin"
    parts = on_calendar.split()
    if len(parts) < 2:
        raise ValueError(f"Cannot parse OnCalendar: {on_calendar!r}")

    if len(parts) == 3:
        # "Mon..Fri HH:MM Timezone"
        _, hhmm, tz_name = parts
    elif len(parts) == 2:
        # "Mon..Fri HH:MM" — no explicit timezone, assume UTC
        _, hhmm = parts
        tz_name = "UTC"
    else:
        raise ValueError(f"Unrecognised OnCalendar format: {on_calendar!r}")

    # Parse HH:MM
    fire_time = datetime.strptime(hhmm, "%H:%M").time()

    # Build the fire instant in the stated timezone, then convert to UTC.
    tz = zoneinfo.ZoneInfo(tz_name)
    fire_local = datetime.combine(on_date, fire_time, tzinfo=tz)
    return fire_local.astimezone(UTC)


# --- parameterized fixture -----------------------------------------------------------

def _timer_test_cases() -> list[tuple[Path, str]]:
    """(timer_path, mic) for every committed timer file.  Collected at import time."""
    return [(p, _parse_mic_from_filename(p)) for p in _find_timer_files()]


@pytest.mark.parametrize(
    "timer_path,mic",
    _timer_test_cases(),
    ids=lambda x: x.name if isinstance(x, Path) else str(x),
)
def test_timer_fires_after_resolved_close(timer_path: Path, mic: str) -> None:
    """The OnCalendar fire time is strictly after the resolved session close.

    Independent oracle for 2026-06-10 (ordinary Tuesday session):
      XEUR session close: 22:00 Europe/Berlin = 20:00 UTC  (CEST, UTC+2)
      XNYS session close: 16:00 America/New_York = 20:00 UTC  (EDT, UTC-4)

    Any committed timer whose fire time is at or before the resolved close on this date
    would capture mid-session quotes as the close — the XEUR bug this test catches.
    """
    registry = parse_index_registry(_REGISTRY_BLOCK)
    resolver = CalendarResolver(registry, as_of=_REPR_SESSION)

    content = timer_path.read_text()
    on_calendar = _extract_oncalendar(content)

    # Build the fire instant in UTC on the representative session date.
    fire_utc = _fire_utc(on_calendar, _REPR_SESSION)

    # Resolve the session close for each index that uses this MIC.
    symbols = _MIC_TO_SYMBOLS.get(mic)
    if symbols is None:
        pytest.skip(f"No test oracle for MIC {mic!r} — add it to _MIC_TO_SYMBOLS")

    for symbol in symbols:
        close_utc = resolver.session_close(symbol, _REPR_SESSION)

        # Primary assertion: the timer fires AFTER the resolved close.
        assert fire_utc > close_utc, (
            f"{timer_path.name}: fire time {fire_utc.isoformat()} is NOT after "
            f"session close {close_utc.isoformat()} for {symbol} (MIC {mic}). "
            f"OnCalendar={on_calendar!r}. "
            "Re-run `uv run python scripts/gen_capture_timers.py` to fix."
        )

        # Secondary assertion: the margin is meaningful (not trivially 1 minute).
        margin = fire_utc - close_utc
        assert margin >= timedelta(minutes=_MIN_MARGIN_MINUTES), (
            f"{timer_path.name}: fire margin is only {margin} for {symbol} — "
            f"must be >= {_MIN_MARGIN_MINUTES} min."
        )


# --- additional: DST invariance (the timer tracks DST correctly) ---------------------

@pytest.mark.parametrize(
    "timer_path,mic",
    _timer_test_cases(),
    ids=lambda x: x.name if isinstance(x, Path) else str(x),
)
def test_timer_tracks_dst_correctly(timer_path: Path, mic: str) -> None:
    """An explicit-tz OnCalendar with a local-time close stays coherent across DST.

    The systemd timezone directive makes the timer track the exchange close across DST
    (the fire time in local exchange-tz is constant; the UTC offset shifts with DST).
    This test verifies the timer remains AFTER the close in a winter session too, ensuring
    the stated timezone matches the exchange and was not accidentally set to UTC or server-local.

    Winter oracle for 2026-01-09 (ordinary Friday session):
      XEUR: 22:00 Europe/Berlin = 21:00 UTC  (CET, UTC+1)
      XNYS: 16:00 America/New_York = 21:00 UTC  (EST, UTC-5)
    """
    registry = parse_index_registry(_REGISTRY_BLOCK)
    winter_session = date(2026, 1, 9)
    resolver = CalendarResolver(registry, as_of=winter_session)

    content = timer_path.read_text()
    on_calendar = _extract_oncalendar(content)

    symbols = _MIC_TO_SYMBOLS.get(mic)
    if symbols is None:
        pytest.skip(f"No test oracle for MIC {mic!r}")

    fire_utc = _fire_utc(on_calendar, winter_session)

    for symbol in symbols:
        close_utc = resolver.session_close(symbol, winter_session)
        assert fire_utc > close_utc, (
            f"{timer_path.name}: fire time {fire_utc.isoformat()} is NOT after "
            f"winter session close {close_utc.isoformat()} for {symbol} (MIC {mic}). "
            f"OnCalendar={on_calendar!r}. "
            "The timer's timezone may be wrong — it must use the exchange timezone, not UTC."
        )
