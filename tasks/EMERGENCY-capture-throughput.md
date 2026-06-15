# T-capture-throughput — ⛔ EMERGENCY — the close walk is too slow to be a *close* snapshot

> **⛔ EMERGENCY (timeliness).** A "close capture" that takes 30–60 min is not a close snapshot —
> it smears across a moving market and risks not finishing inside the settlement window. The
> index-alone canary already took **~7 minutes**; ×11 underlyings (once
> [EMERGENCY-constituent-lane-activation](EMERGENCY-constituent-lane-activation.md) fires) blows past any
> sane post-close window. Fix the throughput before the unattended week runs the full basket.

> **Source:** 2026-06-15 SX5E canary (run_id `89421177611f42ff85b55ba9144f8662`). Collection ran
> 06:56:20 → 07:03:29 UTC (**~7 min, index only**), landing 1,183 events across 12 tenors.

## The bottleneck (latency-bound, not work-bound)

`_discover_chain`
(`packages/infra-ibkr/src/algotrading/infra_ibkr/collectors/cp_rest_close_capture.py:228-265`)
qualifies conids by walking strikes **strictly sequentially**:

```python
for strike in qualified:
    for right in ("C", "P"):
        for contract in discovery.contracts(conid, ..., strike=strike, right=right):  # 1 blocking GET each
```

Each iteration is one `/iserver/secdef/info` round-trip (~0.46 s, almost all network wait). The
canary qualified **587 strike-slots across 12 tenors → ~1,170 blocking calls** for the index alone.
×11 underlyings ⇒ thousands of strictly-serial round-trips. The CPU is idle; we are paying latency,
not compute.

## Why concurrency is safe here (output is provably order-independent)

The discovery calls have **no inter-dependency**, and the assembled output does not depend on the
order they return (`cp_rest_close_capture.py:258-265`):

- `expirations` → `tuple(sorted(set(...)))`
- `strikes` → a `set`, then `sorted`
- `conid_by_contract` → a dict keyed by `(expiry,strike,right)` token
- `multiplier` → the constant `"100"`

So running the `(strike, right)` calls through a **bounded** concurrent pool yields a
**byte-identical** captured basket. The transport's 429/503 backoff
(`cp_rest_transport.py:34-43`) already exists precisely to absorb the CP Gateway's pacing
pushback — a *modest* pool degrades gracefully instead of breaking.

## Scope

- Parallelise the `_discover_chain` `/secdef/info` walk with a **bounded** worker pool (`httpx.Client`
  is thread-safe; a `ThreadPoolExecutor` over the `(strike, right)` pairs keeps the sync transport).
  **Bounded, not unleashed** — the CP Gateway is a single paced session; default pool small (≈6),
  **typed-config** knob (ADR 0028, no `.py` literal), so we can tune against real 429 behaviour
  without a code change.
- Keep the existing 429/503 backoff as the pacing valve; surface a structured log of retries so we
  can see whether the pool is over-pacing.
- Optional second lever (only if concurrency alone is short): an **intraday conid cache** keyed by
  `(underlying_conid, month) → {strike,right: conid}` — `strike→conid` is static within a session,
  so re-runs / the constituent walk skip already-resolved discovery. More state; do only if needed.

## Guardrails (this is where "without breaking everything" lives)

1. **Output parity is the acceptance bar:** a fixture-driven test must assert the concurrent walk
   produces the **identical** `AvailableChain` + `conid_by_contract` as the sequential walk. If it
   isn't identical, it's wrong.
2. **Do not touch the strike window.** The full-30Δ uncapped band is an owner ruling
   (`cp_rest_chain_window.py:17-21`, 2026-06-12) — making **fewer** calls by re-clipping is exactly
   the intent-vs-delivery bug that was killed. The win is "the same calls, faster," never "fewer
   calls."
3. Conservative pool default; prove the gateway tolerates it before raising it. A flood of 429s
   that the backoff serialises back to ~sequential is a net loss.

## Orthogonality / seams

- **Shares the file** `cp_rest_close_capture.py` with
  [EMERGENCY-quote-integrity-gate](EMERGENCY-quote-integrity-gate.md) but a **different function** (this owns
  `_discover_chain`; that owns the snapshot kept/drop + normalize). Serialize on the file per the
  TASKBOARD; concerns are disjoint.
- Pure performance — it changes **how fast** the same basket is captured, not **what** is captured
  (quote-integrity) or **which underlyings** (constituent-lane). Land alongside the constituent lane,
  whose ~11× fan-out is what makes this load-bearing.

## Done criteria

- The concurrent `_discover_chain` is **output-identical** to the sequential walk on a fixture
  (locked by test).
- Wall-clock for the index + 10 constituents fits comfortably inside the post-close settlement
  window with margin (target: well under the window, not "barely").
- Pool size is typed config; 429 retry behaviour is observable in logs; no increase in dropped or
  unresolved contracts. Gate green.
