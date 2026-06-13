# T-capture-config-coherence — retire/reconcile the orphaned capture.yaml (+ stale log-only underlyings)

> **✅ DONE 2026-06-13 (T-index-only-refactor Phase 4).** Both halves resolved:
> - **`capture.yaml` retired** — verified fully orphaned before deleting: no Python loader binds
>   it (the sibling `ibkr_history.yaml` *has* a real `load_ibkr_history_config`; capture.yaml had
>   no equivalent), the `strike_selection` collector module its header cited does not exist, and the
>   live CP-REST path builds `ChainSelection` from `universe.yaml` via `_selection_from_config`
>   (`cp_rest_close_capture.py`). The capture span now has ONE source: `universe.yaml`
>   (`tenor_grid` + `strike_selection`). File deleted; no two configs can disagree.
> - **Stale `underlyings` list** — removed at the root in Phase 3 (the whole `UniverseConfig.underlyings`
>   field is gone, not just the log bind). The index registry is the single universe source.
>
> Original write-up (the 2026-06-12 intent-vs-delivery audit, findings Cap-3 / Lane-0) below.

> **From the 2026-06-12 intent-vs-delivery audit** ([report](AUDIT-INTENT-VS-DELIVERY-2026-06-12.md),
> findings Cap-3 / Lane-0). **LATENT MINE, not an active bug** — these values do not drive the live
> capture today; the danger is that a reader (or the legacy streaming collector) honors them, or a
> future refactor wires them, and silently re-clips the term structure. Coherence cleanup in the
> spirit of [index-addition coherence](../.agent/decisions/) (one source per policy, no drifting
> parallel copies).

## The smell — two capture configs that disagree, one of them dead

`packages/infra-ibkr/configs/capture.yaml` `collection:` block encodes a term structure that
**contradicts** `universe.yaml tenor_grid` (`[10d…3y]`, 8 tenors):

| key | value | conflict |
|---|---|---|
| `n_expiries` (l.9) | `4` | 4 nearest expiries cannot span 8 pinned tenors |
| `min_days` (l.11) | `25` | **excludes the 10d tenor entirely** |
| `max_days` (l.12) | `90` | **clips the horizon to ~3 months** — 6m/12m/18m/2y/3y impossible |

But the live CP-REST close-capture path **does not read this block** — it builds `ChainSelection`
from `universe.yaml` via `_selection_from_config` (`cp_rest_close_capture.py:676`). So the drifted
values are *dead for the live path* yet actively misleading. **The fix is coherence, not numbers:**
either retire `capture.yaml`'s `collection:` block (if nothing loads it — verify no loader binds it),
or wire it as the single capture-span source and delete the duplication in `universe.yaml`. One
source for the capture span, no silently-clipping shadow copy.

## Folded in — Lane-0: stale `universe.yaml underlyings` (cosmetic, log-only)

`configs/universe.yaml:10 underlyings: [AAPL, MSFT, SPY]` is a stale equity universe matching neither
the SP500+SX5E intent nor the live capture. **Severity downgraded (owner-verified 2026-06-12):** it
is read **only as a structured-log field** (`jobs.py:128 _LOGGER.bind(..., underlyings=...)`) — it
does **not** drive capture (the SX5E/SPX index registry + injected masters do). So this is a polluted
log label, not a data bug. Clean it up while reconciling capture config so logs reflect reality.

> **Note — "top-10 ATM constituents" has no typed home.** The course's top-10 constituent ATM policy
> lives implicitly in `data/reference/index_constituents` + the SSGA seed, with no config parameter
> (ADR-0028 gap). Out of scope for the cleanup but flag for a later typed-config pass.

## Done criteria

`capture.yaml collection:` is either removed (confirmed no loader) or made the single capture-span
source with the `universe.yaml` duplication removed; no two configs disagree about the term
structure; the log-only `underlyings` list reflects the live universe (or is dropped from the log
bind); gate green.
