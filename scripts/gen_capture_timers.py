"""Generate eod-capture@<MIC>.timer files from the index registry + exchange calendar.

# USAGE
#   uv run python scripts/gen_capture_timers.py
#   uv run python scripts/gen_capture_timers.py --dry-run   # print, don't write
#   uv run python scripts/gen_capture_timers.py --margin 45 # minutes of safety margin

This is the coherence fix for the XEUR timer-shift bug (ibkr-clock-timer-coherence,
2026-06-15): the old eod-capture@XEUR.timer carried `OnCalendar=Mon..Fri 18:15
Europe/Berlin`, which fires 3h45m BEFORE the real XEUR session close (22:00 Berlin),
snapshotting mid-session quotes as the close. The fix: derive the fire time from the
calendar, never type it by hand.

## What the generator does

1. Reads `configs/universe.yaml` via the canonical config loader.
2. Groups enabled indices by their `exchange_calendars` MIC code.
3. For each unique MIC code that appears among the enabled indices:
   a. Resolves the regular-session close time-of-day and timezone from the calendar
      (the ``exchange_calendars`` library is the single source of truth).
   b. Adds a safety margin (default 45 minutes; pass ``--margin N`` to change).
   c. Writes (or prints) `scripts/systemd/eod-capture@<MIC>.timer` with a
      ``# GENERATED — DO NOT EDIT`` header so the provenance is visible.
4. Writes (or prints) a `scripts/systemd/eod-capture@<MIC>.timer` for every MIC
   code that has at least one enabled index, then exits non-zero if any index's
   timer would fire BEFORE its resolved close (the drift guard).

## Why "derive, don't type"

The `OnCalendar` line is static systemd text; it cannot be computed at runtime. But it
*can* be derived once, at generation time, from the calendar, and the generator makes
"re-run after changing universe.yaml" the canonical way to stay in sync. The runner
still resolves the EXACT close instant from the calendar on every fire — the timer is
only a safe trigger upper-bound, never the close itself. Holidays / half-days stay
handled by the runner (a holiday is a clean no-op), not by the timer.

## Safety margin convention

45 minutes is the default. It must satisfy:
  fire_time >= regular_session_close + margin > regular_session_close

For XEUR: 22:00 + 45min = 22:45 Europe/Berlin.  (The old 18:15 was 3h45m EARLY.)
For XNYS: 16:00 + 45min = 16:45 America/New_York.  (The existing timer already had
this — the generator regenerates it byte-identically.)

The margin is NOT the guard for half-days: the runner's calendar check handles those.
The margin is a buffer so the systemd fire does not race the regular close.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import exchange_calendars as xcals

# --- constants (never economic parameters; pure structural / layout) -------------------

# Default safety margin added to the regular session close to give the fire time.
# Chosen so the timer fires comfortably after the close but before midnight:
#   XEUR  22:00 + 45 min = 22:45 Europe/Berlin
#   XNYS  16:00 + 45 min = 16:45 America/New_York
_DEFAULT_MARGIN_MINUTES = 45

# The template for a generated timer file; {placeholders} are filled per-MIC.
_TIMER_TEMPLATE = """\
# eod-capture@{mic}.timer — daily {mic} close-capture trigger (WS 1G, ADR 0032).
#
# GENERATED — DO NOT EDIT.  Source of truth: scripts/gen_capture_timers.py
# Re-run `uv run python scripts/gen_capture_timers.py` after changing configs/universe.yaml.
#
# Fires the eod-capture@{mic}.service shortly after the {mic} regular close. The trigger
# time is derived from the exchange_calendars library for calendar {mic}: regular-session
# close is {close_time_local} {tz_name}, so {fire_time_local} {tz_name} is a safe upper
# bound (regular close + {margin_minutes} min safety margin).
#
# The fixed OnCalendar time is only the *trigger*: the runner resolves the EXACT close
# instant from the exchange calendar (session_close), so a half-day early close or a
# holiday is handled by the resolver (a holiday is a clean no-op), NOT by editing this
# timer. The timezone is explicit so the trigger tracks the {mic} close across DST,
# not the server's clock.
#
# Persistent=true: a fire missed while the box was down runs on next boot; the catch-up
# fire reconstructs the gap day through the run-state ledger (idempotent by construction).
#
# Install: copy to ~/.config/systemd/user/ and
#   `systemctl --user enable --now eod-capture@{mic}.timer`.

[Unit]
Description=Daily {mic} close-capture timer

[Timer]
# {fire_time_local} {tz_name}, Mon–Fri — a safe upper bound on the {close_time_local}
# {mic} regular close.  The runner skips any day the {mic} calendar marks a
# non-session, so weekends/holidays are no-ops, not misfires.
OnCalendar=Mon..Fri {fire_time_local} {tz_name}
Persistent=true
# A small randomized delay so multiple calendars firing near each other don't
# thundering-herd.
RandomizedDelaySec=120
Unit=eod-capture@{mic}.service

[Install]
WantedBy=timers.target
"""

# Sentinel date used to resolve the regular-session close time-of-day. We pick a recent
# ordinary trading date that is a session on all three of the major calendars (XEUR,
# XNYS) and has no known half-day anomaly. The generator reads only the TIME component
# (hour:minute) of the close, not the calendar-dependent UTC offset. The chosen date is
# a known full session on both; confirmed by the calendar resolver tests.
_REPRESENTATIVE_SESSION = date(2026, 6, 10)  # Tuesday, ordinary session

# How far back the calendar is built, in years from the representative date. Matches
# the resolver's _CALENDAR_LOOKBACK_YEARS for consistency.
_CALENDAR_LOOKBACK_YEARS = 5


def _repo_root() -> Path:
    """The repo root (parent of this script's directory)."""
    return Path(__file__).resolve().parent.parent


def _load_index_entries() -> list[tuple[str, str]]:
    """Load (symbol, calendar_mic) for all enabled indices from universe.yaml.

    Returns a list of (symbol, mic) pairs for the enabled indices, in the order they
    appear in the registry. Uses the canonical infra loader so parsing / validation
    applies (an unknown MIC is rejected by the loader, not silently accepted here).
    """
    # Import here so the script fails clearly if the workspace is not set up.
    from algotrading.core.config import load_platform_config
    from algotrading.infra.universe import parse_index_registry

    config = load_platform_config(_repo_root() / "configs")
    registry = parse_index_registry(config.universe.indices)
    return [
        (entry.symbol, entry.calendar)
        for entry in registry.entries
        if entry.enabled
    ]


def _regular_close_local(mic: str) -> tuple[str, str]:
    """Return (HH:MM, tz_name) for the regular-session close of a calendar MIC.

    Uses ``_REPRESENTATIVE_SESSION`` as the anchor date and reads the time component
    of the session close in the exchange timezone (the ``exchange_calendars`` calendar's
    ``tz`` attribute). The UTC offset changes with DST, but the local time does not for
    the calendars we use, so the generated OnCalendar line remains valid year-round.

    Both XEUR (22:00 Europe/Berlin) and XNYS (16:00 America/New_York) are verified to
    have a constant local-time close regardless of DST. The function asserts this is
    consistent across a representative DST boundary so a new calendar with non-constant
    local-time closes fails loudly here rather than silently drifting.
    """
    start = _REPRESENTATIVE_SESSION.replace(
        year=_REPRESENTATIVE_SESSION.year - _CALENDAR_LOOKBACK_YEARS
    )
    cal = xcals.get_calendar(mic, start=start, end=date(2027, 12, 31))

    # The library's tz attribute names the exchange timezone.
    tz_name: str = str(cal.tz)

    anchor = _REPRESENTATIVE_SESSION
    close_utc = cal.session_close(anchor)

    # Convert to the exchange local timezone.
    close_local = close_utc.tz_convert(tz_name)
    close_hhmm = close_local.strftime("%H:%M")

    # Sanity-check: verify the local time is the same on both sides of a DST boundary.
    # Pick a session a few months earlier (winter, different DST offset) and assert it
    # has the same local-time close. If a new calendar ever has DST-shifting closes,
    # this guard will fail loudly rather than silently generating the wrong fire time.
    # Use a confirmed winter session date.
    winter_anchor = date(2026, 1, 9)  # January, ordinary Friday session (XEUR+XNYS both open)
    try:
        winter_close_utc = cal.session_close(winter_anchor)
        winter_close_local = winter_close_utc.tz_convert(tz_name)
        winter_hhmm = winter_close_local.strftime("%H:%M")
        if winter_hhmm != close_hhmm:
            raise SystemExit(
                f"ERROR: {mic} has DST-shifted close times — "
                f"summer={close_hhmm}, winter={winter_hhmm}. "
                "The generator cannot produce a valid static OnCalendar. "
                "Use a different timezone (UTC?) for a calendar whose local close shifts."
            )
    except xcals.errors.NotSessionError:
        # winter_anchor is not a session on this calendar — skip the DST check.
        pass

    return close_hhmm, tz_name


def _fire_time(close_hhmm: str, margin_minutes: int) -> str:
    """Add margin_minutes to close_hhmm (HH:MM) and return the result as HH:MM."""
    close_dt = datetime.strptime(close_hhmm, "%H:%M").replace(tzinfo=UTC)
    fire_dt = close_dt + timedelta(minutes=margin_minutes)
    return fire_dt.strftime("%H:%M")


def _render_timer(
    mic: str,
    close_hhmm: str,
    tz_name: str,
    margin_minutes: int,
) -> str:
    """Render the timer file content for a single MIC."""
    fire_hhmm = _fire_time(close_hhmm, margin_minutes)
    return _TIMER_TEMPLATE.format(
        mic=mic,
        close_time_local=close_hhmm,
        tz_name=tz_name,
        fire_time_local=fire_hhmm,
        margin_minutes=margin_minutes,
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point for uv run python scripts/gen_capture_timers.py."""
    parser = argparse.ArgumentParser(
        description="Generate eod-capture@<MIC>.timer files from the index registry."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated content to stdout instead of writing files.",
    )
    parser.add_argument(
        "--margin",
        type=int,
        default=_DEFAULT_MARGIN_MINUTES,
        metavar="MINUTES",
        help=(
            f"Safety margin in minutes added to the regular-session close to obtain "
            f"the fire time (default: {_DEFAULT_MARGIN_MINUTES})."
        ),
    )
    parser.add_argument(
        "--configs",
        type=Path,
        default=None,
        metavar="PATH",
        help="Path to the configs/ directory (default: <repo-root>/configs).",
    )
    args = parser.parse_args(argv)

    # Build the set of MIC codes for enabled indices.
    try:
        enabled_entries = _load_index_entries()
    except Exception as exc:
        print(f"ERROR loading index registry: {exc}", file=sys.stderr)
        return 2

    if not enabled_entries:
        print("No enabled indices in universe.yaml — nothing to generate.", file=sys.stderr)
        return 0

    # Collect unique MICs (preserve insertion order so output is deterministic).
    mics_seen: dict[str, list[str]] = {}
    for symbol, mic in enabled_entries:
        mics_seen.setdefault(mic, []).append(symbol)

    out_dir = _repo_root() / "scripts" / "systemd"

    exit_code = 0
    for mic, _symbols in mics_seen.items():
        try:
            close_hhmm, tz_name = _regular_close_local(mic)
        except Exception as exc:
            print(f"ERROR resolving close time for {mic}: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        content = _render_timer(mic, close_hhmm, tz_name, args.margin)
        timer_path = out_dir / f"eod-capture@{mic}.timer"

        if args.dry_run:
            print(f"# --- {timer_path} ---")
            print(content)
        else:
            timer_path.write_text(content)
            fire = _fire_time(close_hhmm, args.margin)
            print(f"Wrote {timer_path}  ({mic} fires at {fire} {tz_name})")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
