# T-task-coherence-audit — backlog coherence vs blueprint + transcripts (2026-06-14)

> A multi-agent audit run (19 core/infra/ibkr tasks; one sonnet auditor each + an adversarial
> sonnet verifier on every flag) cross-checked each task's framing/scope/premise against the
> **blueprint** (`documentation/blueprint/`, ADR 0011) and the **transcripts**
> (`documentation/transcripts/`). Goal: catch mis-framed / stale tasks **before** implementing them
> — after the `strike-window` case showed a task can be well-intentioned but mis-aligned with the
> sources + the deliberate code design.

## Result: the backlog is sound — **16 / 19 coherent**

The adversarial verifier **refuted 4 audit false-positives** (it declined to wrongly rescope
`core-config-effective-dating`, `core-pricing-config-completeness`, `infra-daily-bar-compaction`,
`infra-mirror-greeks-putcall` — all confirmed coherent). That double-pass is why the run is
trustworthy. **No task required abandoning work.**

## 3 tasks rescoped (verifier upheld the flag) — applied 2026-06-14

1. **`2C-pnl-attribution`** — premise **stale**: `risk/attribution.py` already shipped 2026-06-07
   (commit `4e3f50f`). STATUS banner added: verify 2C done-criteria / list real gaps, do not re-build.
2. **`T-scenario-rate-axis`** — motivation **over-claimed**: "the course prescribed a rate axis"
   rests on a **garbled transcript line** (`Consignes.txt l.119` = *"…de l'éducation"*, not "taux").
   Reframed: the rate axis is a *reasonable inference* + a **blueprint §5 optional extension**, not a
   course prescription. Engine + config already landed. (Also corrected the architecture transcript
   doc to flag `l'économie → taux` as an inference, not a clearly-spoken word.)
3. **`T-second-order-greeks`** — gap section **stale** (Vanna/Volga/Charm landed). STATUS banner:
   step 3 (front carry) only.

## Greenlit to implement (coherent; sources aligned)

`core-explicit-rate-config` (step 2), `core-pricing-config-completeness` (remaining slices),
`core-config-effective-dating` (the core-side `load_platform_config` as-of half genuinely missing),
`ibkr-clock-timer-coherence`, `ibkr-constituent-option-capture`, `ibkr-option-volume-capture`,
`ibkr-unattended-reauth`, `infra-daily-bar-compaction`, `infra-mirror-greeks-putcall`,
`infra-per-side-surfaces`, `infra-rates-curve-ingest`, `infra-raw-invariant`, `infra-rt-vega`
(⏳ owner ruling on the annualisation convention — `vega/√T`?), `infra-named-scenarios-and-corr-shock`,
`infra-signal-layer`, `infra-strike-window-pct-clip` (fix = guaranteed-superset / fail-loud, **not**
the config-home — that option conflicts with the deliberate technical-bound design).

## Run cost
26 sonnet agents, ~610k tokens, ~4 min. No opus in the fan-out; synthesis done in the main loop.
Not yet audited (deferred): `strategy-*`, `execution-*`, `frontend-*` (blocked / claimed lanes).
