# Task: derive the EOD capture timers from the calendar (kill the timer↔close drift)

**Status:** done (2026-06-15, branch `ibkr-clock-timer-coherence`). Guard hotfix `07c892d`
confirmed live as an ancestor of HEAD before the XEUR timer was moved. Gate green (2202 passed, 12 skipped).

## Why

Each `eod-capture@<MIC>.timer` carries a **hand-written `OnCalendar` time** that can drift from
the calendar's real `session_close`. That drift is the root of the XEUR bug: the timer fires
18:15 CEST for a "17:30 cash close" while `exchange_calendars` XEUR `session_close` is 22:00 CEST
(Eurex derivatives), so SX5E is snapshotted ~3h before its resolved close and the capture is a
mid-session set stamped as the close. Adding an index "properly" must not require writing a timer
time that can lie — see the `index-addition-coherence` owner principle.

This is a **coherence** fix, not a one-off: one source of truth (registry + calendar), no
per-index hand-set value that drifts.

## Design (decide before coding — do NOT just hand-rewrite XEUR's time)

`OnCalendar` is static systemd text, so "derive it at runtime" is not literal. Options:

- **(a) — preferred: a generator `scripts/gen_capture_timers.py`.** Reads `configs/universe.yaml`;
  for each calendar of the enabled indices, computes the regular-session close time-of-day in the
  exchange tz **+ a safety margin**, and writes each `eod-capture@<MIC>.timer` with a
  `# GENERATED — DO NOT EDIT` header. Fire time is then derived from the calendar, never typed.
  "Add an index" = re-run the generator. Half-days/holidays stay handled by the runner (the timer
  is only a safe upper bound), as today. This is the coherence answer.
- **(c) — minimum net, ship even if (a) waits: `test_timer_fires_after_resolved_close`.** Parse
  each committed `.timer`'s `OnCalendar`, convert to UTC for a representative session, assert it is
  `>= resolver.session_close(index, session)` for every enabled index on that calendar. Catches
  drift (incl. the XEUR bug) even on a hand edit.
- (b) optional belt: a pre-fire check in the runner that refuses/warns if it fires before the
  resolved close. Complement to (a), not a replacement.

## Guardrails

1. Propose the (a) design for sign-off before coding.
2. Do **not** move the XEUR timer to its post-22:00 close until the guard hotfix is confirmed live
   on the server tree — else SX5E breaks (today it captures early but non-empty).
3. Not urgent: SX5E mid-session capture is non-regressive (it already does this). Do it calmly.

## References

- Root cause + hotfix detail: memory `spx-post-close-drop-bug`, `index-addition-coherence`.
- Owner ruling OQ-2 (guard→timer sequencing): memory `audit-oq-rulings`.
- Timer/service units: `scripts/systemd/eod-capture@XEUR.timer`, `scripts/systemd/eod-capture@XNYS.timer`,
  `scripts/systemd/eod-capture@.service`. (`documentation/connectivity/` was removed; the live units
  are tracked in `scripts/systemd/`.)
