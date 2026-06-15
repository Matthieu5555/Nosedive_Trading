# T-quote-integrity-gate — ⛔ EMERGENCY — refuse to bank a last-only / closed-market capture as a real close

> **⛔ EMERGENCY.** Everything downstream (surfaces, Greeks, the strategy book, P&L) is only as
> trustworthy as the close capture. The 2026-06-15 canary proved the pipeline will **happily fit a
> surface and bank analytics off junk** and only flag it *after the fact*. The "discard the
> bullshit" half of the capture mandate has no enforcement at the capture boundary. Close it before
> the unattended week, or every banked day is a coin-flip.

> **Source:** 2026-06-15 SX5E canary (paper account, run_id `89421177611f42ff85b55ba9144f8662`,
> temp store `/tmp/sx5e-canary.QNKI`). The conversation that found it: index capture landed
> end-to-end, QC went `failed`, but a full surface + 636 projected-analytics rows were still
> written.

## What the canary showed (the hard evidence)

The run was against a **closed** market. Every one of the 1,163 option/index snapshot rows
(`snapshot/market_state_snapshots`) carries:

- `bid <= 0` **1163/1163**, `ask <= 0` **1163/1163**, `bid == ask` **1163/1163** — i.e. **no
  two-sided quote at all**; only `last` is real (`last > 0` on all rows).
- `completeness = 0.3333` on **every** row (1 of 3 quote components present).
- `flags = ["closed","fallback_spot"]` on **every** row.

Despite that, the pipeline:
- solved 1,162 IV points off `last` alone (IV 0.137–0.253 — *plausible-looking*, which is the
  trap), fit a **converged, arb-free** surface on all 13 maturities, and wrote forward curve +
  636 `projected_option_analytics` rows;
- the **only** brake was QC flagging `delta_band_completeness` (critical, 5 interior gaps) and 12
  `surface_fit_error` *warnings* (every maturity hit a parameter bound — `a_lower`/`rho_lower` —
  the classic thin-data degeneracy). **All of that still persisted to the store.**

So a last-only, market-closed snapshot produced an artefact that *looks* like a real surface.
That is exactly the "bullshit" the capture is supposed to discard, and nothing stopped it.

## The gap

There is **no capture-time integrity gate** that distinguishes:

1. **"market closed / no live quotes"** → this is a *labelled no-capture* (like the optionless
   `collect_*_basket` → `None` path), **not** a close to bank. Today it banks anyway.
2. **single-sided / zero-spread / stale rows** inside an otherwise-live basket → these should be
   **quarantined** (kept in `raw/` for audit, excluded from the derived close set) with a recorded
   reason, not silently fed to the IV solver.
3. **a genuine two-sided close** → bank it.

`completeness` already exists as a per-row scalar (0.333 here) but is **inert** — nothing reads it
as a gate. `flags=["closed"]` is recorded but **not acted on**.

## Scope

- A **capture-time quote-integrity decision** in the close-capture path
  (`packages/infra-ibkr/src/algotrading/infra_ibkr/collectors/cp_rest_close_capture.py`,
  `_snapshot_events` / the kept-rows pass + `cp_rest_normalize.py`): classify each snapshot row by
  quote health (two-sided & sane spread / single-sided / zero / stale / closed) and **promote only
  healthy rows to the derived close set**, recording the drop reason per row (the audit trail — the
  "discard the bullshit" receipt). Raw stays immutable (ADR 0040): nothing is deleted from `raw/`,
  the gate is on promotion to derived.
- A **basket-level verdict**: if the *whole* basket is closed/last-only/degenerate (this canary),
  return the **labelled no-capture** (`None` + structured reason), **not** a fit. Mirror the
  existing loud-fail discipline — a wrong-day capture already raises `CloseCaptureError`; a
  closed-market capture should be an explicit `flags=["closed"]`-driven no-op, never a banked
  surface.
- Promote `completeness` + `flags` from inert scalars to a **hard floor** wired from typed config
  (`configs/qc.yaml` / `universe.yaml`, ADR 0028 — no `.py` literal), e.g. a minimum two-sided
  fraction per tenor below which the basket is refused. Coordinate the threshold with the existing
  `delta_band_completeness` / `underlying_quote_health` QC so the capture gate and the QC gate tell
  the **same** story (capture refuses up front; QC stays the end-to-end backstop).
- Extend `underlying_quote_health` (today index-only — it **passed** on this run while every option
  was zero-bid) to cover the **option** legs, so "the chain has no two-sided quotes" is itself a
  critical, not a silent pass.

## Orthogonality / seams

- **Shares the file** `cp_rest_close_capture.py` with
  [EMERGENCY-capture-throughput](EMERGENCY-capture-throughput.md) but a **different function** (this owns the
  snapshot kept/drop + normalize; throughput owns `_discover_chain`). Serialize on the file per the
  TASKBOARD; the concerns do not overlap.
- Independent of [EMERGENCY-constituent-lane-activation](EMERGENCY-constituent-lane-activation.md) (that is
  *which underlyings* get captured; this is *whether a captured row is real*).

## Done criteria

- A closed / last-only / all-zero-bid basket (the canary input) is **refused or fully quarantined
  with a recorded reason** — never landed as a clean close; the runner exits with an honest
  "no live quotes" outcome, not a green-looking surface.
- A genuine two-sided close passes untouched (regression: a fixture with real bid/ask is
  byte-identical to today).
- The `surface_fit_error` bound-hit warnings and the `delta_band_completeness` interior gaps **do
  not occur** on real two-sided data (they were thin-data artefacts, not band-policy failures).
- `completeness`/`flags` are enforced from typed config; `underlying_quote_health` covers option
  legs; gate green.
