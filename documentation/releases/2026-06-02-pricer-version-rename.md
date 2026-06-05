# Release: 2026.06-pricer-version-rename

- **Date:** 2026-06-02
- **Author:** Matthieu

This is the human-readable half of a provenance-tag correction, filed per
[`../release-management.md`](../release-management.md). It is the first entry in
`documentation/releases/`; future release artifacts live beside it, findable from the tag.

## What changed

`PRICER_VERSION` renamed from `black76-crr-1.0.0` to `black76-lr-1.0.0` in
`backend/src/pricing/engine.py`. Label-only correction: the American engine is and
always has been Leisen-Reimer (`backend/src/pricing/american.py`), never
Cox-Ross-Rubinstein, so `crr` was a misnomer in the tag. No price, Greek, or
scenario formula changed.

## Why

The provenance tag named the wrong lattice. `PRICER_VERSION` is stamped into every
`PricingResult.pricer_version`, so the wrong tag is a traceability bug: a reader
auditing a number would conclude it came from a CRR tree this codebase has never
contained.

## Config

No config section bumped. `PRICER_VERSION` is a code constant, not a
`configs/default.toml` section, and the patch level stays `1.0.0` — no formula moved,
and ADR 0004 decision 1 reserves a bump for "a real change to the price or Greek
formulas." `config_hash` is unchanged.

## Tests passed

Full gate green: `ruff` clean, `mypy` clean (125 source files), `pytest` 584 passed.
The byte-identical replay (`test_replay_byte_identical`) and both determinism goldens
(`test_determinism_analytics`, `test_determinism_risk`) pass against the committed
goldens *without regeneration* — confirming no committed stamp hash depends on the
version string, so no golden and no number moved.

## Periods revalidated

None required. By the cheap test in `release-management.md` (byte-identical replay
before and after), this is a plumbing rename, not an economics change: no number and
no committed hash moved. The only forward-looking effect is the human-readable
`pricer_version` label — results emitted on or after 2026-06-02 read
`black76-lr-1.0.0`; earlier results keep `black76-crr-1.0.0`. The two label the
identical computation. This note exists so that label discontinuity is explained, not
mysterious, to anyone diffing pricing rows across the boundary.
