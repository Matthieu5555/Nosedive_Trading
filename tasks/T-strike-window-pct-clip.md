# T-strike-window-pct-clip — the %-of-spot fallback window can silently clip the 30Δ band at high vol

> **From the 2026-06-12 intent-vs-delivery audit remediation, handed off by the owner after
> [T-delta-window](T-delta-window.md) landed (gate 1404/0/16, 2026-06-12).** A **second** technical
> request-shaping bound sits in front of the economics on the capture path — the *same* intent-vs-
> delivery class as the delta-window bug just killed, one layer over. **Latent mine, not an active
> bug:** at realistic vols it does not clip, so it is deliberately left as-is. This fiche records the
> condition under which it *would* bite and what's missing (labelling + delivery test).

## The residual bound

`_plan_strikes` (`packages/infra/.../universe/chain_planning.py:564`) uses the economic
`select_strikes_delta_band` on the production band path, but **falls back to the %-of-spot
`select_strikes`** in two places:

- `:585` — when `band is None` / `band.markets` empty (the discovery fallback).
- `:591` — per expiry, when that expiry has no finite-forward band market.

`select_strikes` (`:326`) keeps strikes inside `spot ± strike_window_pct` (clip at `:347-348`), where
`ChainSelection.strike_window_pct` defaults to **`0.35`** — a `.py` literal (`:90`), *not* a typed
config value. The code is already self-aware of the smell (`:476`, `:492` comment: the window "lives
in code").

## When it bites (the intent-vs-delivery condition)

At realistic vols the ±30Δ band stays inside ±35%, so the fallback is a harmless superset:

- 30Δ band at 3y ≤ ~±18.5% at σ≈0.40 (the conservative `discovery_working_vol` seed) < 0.35 → no clip.

**But the band widens with vol·√T.** At **σ ≳ 0.23 at 3y** the 30Δ band exceeds ±35%, so on a
high-vol regime (or a longer tenor) `select_strikes` would **silently trim the 30Δ band** on any
expiry that takes the fallback path — exactly the pattern killed in T-delta-window, relabelled.

## Why it's a mine, not a bug today

The economic band path (`select_strikes_delta_band`) is the production EOD route; the %-of-spot
window is only the fallback. The current backstop is the **`delta_band_completeness` QC check**, which
reads the *delivered final deltas* end-to-end and so catches *any* clip regardless of which bound
caused it. What's missing is the same thing that hid the first two seeds: the **bound itself is
neither labelled (fail-loud) nor tested for its delivered economic reach** — only the mechanism (the
%-window) is exercised. If a refactor ever made the fallback the primary path, or vols spike, it
re-arms silently.

## Fix direction (when prioritised)

- Make `strike_window_pct` a **typed config value** (ADR 0028), not a `.py` literal — one home for the
  capture-window policy. (Regenerates the universe config-hash golden by design, C7 pattern.)
- Either widen/derive the fallback window from the same delta seed so it is a **guaranteed superset**
  of the 30Δ band at the configured working vol (mirror `discovery_delta_bound`), or make it
  **fail-loud** (a `DiscoveryRunawayError`-style valve) rather than trim silently when the band would
  exceed it — never a silent clip.
- Add a delivery test at high σ / long tenor asserting the fallback does **not** drop strikes inside
  the ±30Δ band (the test the first two seeds lacked).

## Done criteria

`strike_window_pct` is typed config; the %-of-spot fallback is a guaranteed superset of the 30Δ band
(or fails loud), never a silent trim; a high-vol/long-tenor delivery test locks it; gate green.
`delta_band_completeness` remains the end-to-end backstop.
