# T-qc-residual-units — forward/parity QC thresholds are absolute-$ on a 7400-pt index (always-FAIL)

> **✅ ALREADY LANDED (verified 2026-06-13).** The fix is in: `configs/qc.yaml` carries
> `max_rel_residual_mad: 0.01` / `max_rel_parity_residual: 0.02` (relative-to-forward), the config
> model has those fields, and `qc/checks.py` computes `relative_residual_mad = residual_mad /
> forward` against `max_rel_residual_mad` — the forward self-label and the QC gate now share one
> relative basis (An-1 resolved too). The board's "active #1" row is stale. Archive-ready.

> **From the 2026-06-12 intent-vs-delivery audit** ([report](AUDIT-INTENT-VS-DELIVERY-2026-06-12.md),
> findings An-1 / An-2 / QC-4). **Owner-confirmed #1 priority (2026-06-12)** — the unit mismatch
> makes the forward/parity QC checks fire on *every* index slice, so it **masks the real QC health
> and pollutes the coverage panel we just shipped** ([T-capture-coverage-panel](T-capture-coverage-panel.md)).
> A re-capture will **not** turn these green. This is a units bug, not a data bug.

## The bug (verified on real 2026-06-11 SPX data, spot ~7400)

Two QC checks compare an **absolute price residual** against an **absolute-$ threshold** tuned for
small-price equity options. On a 7400-point index the natural "good" residual is O(1–100) pts, so
the threshold is unmeetable and the check is a **permanent false-positive** — operators learn to
ignore it.

- `check_parity_residual` (`packages/infra/.../qc/checks.py:281-324`) vs
  `qc.yaml:53 max_parity_residual: 0.10`. Measured SPX residuals: **105.4 / 67.7 / 3.9 / 2.5** —
  even the *good* slices (2.48) blow past 0.10.
- forward residual check (`qc/checks.py:254`) vs `qc.yaml:51 max_residual_mad: 0.05`. Measured
  `residual_mad = 0.159`.

### Folded in — An-1: the self-label contradicts the QC gate (same root)

The forward diagnostic self-labels quality on a **relative** basis
(`forwards/estimate.py:193 relative_residual = residual_mad / forward`; thresholds
`pricing.yaml:28-29 good_rel_residual 1.0e-3 / fair_rel_residual 1.0e-2`), so `residual_mad=0.159`
→ `0.159/7400 ≈ 2e-5` → labelled **"good"**, while QC fails the same number at the absolute 0.05.
**Two different axes for the same question** ⇒ a 3×-over-threshold forward reads "good" in the
diagnostic and "FAIL" in QC. Fix both with **one residual basis**.

## Why the gate stayed green / why it hid

Tests feed hand-built residuals at the $0.10 scale (e.g. 0.03 pass / 0.30 fail) — they validate the
*comparison mechanism*, never an index-scale chain. The check is "green-when-wrong" inverted
(red-always), which is just as blind: a check that always fails carries no signal.

## Fix direction

- Express the forward/parity thresholds **relative to the forward** (fraction / bps) or in **vol
  points**, not absolute $ — so the same economic tolerance holds across a 200-pt single-name and a
  7400-pt index. Typed config (ADR 0028); regenerate the qc config-hash golden by design.
- **Reconcile the residual basis** so the `forwards/estimate.py` self-label and the QC gate use the
  *same* relative definition (kill the rel-vs-abs split). One source of truth for "is this forward
  trustworthy".
- Add a test that asserts the delivered verdict on a **realistic index-scale** residual set (not
  just $-scale fixtures).

## Out of scope (do not conflate)

`calendar_sanity` failing on the ultra-short (5–11d) slices is **not** a units bug — it is a symptom
of the tenor root ([T-tenor-selection](archive/T-tenor-selection.md), landed `74d2cc7`) and should clear
once a real term structure is re-captured. Keep it separate.

## Done criteria

Both checks express the threshold relatively; the forward self-label and QC gate agree on one basis;
a real-index-scale test asserts the delivered verdict; qc config-hash golden regenerated; gate green.
