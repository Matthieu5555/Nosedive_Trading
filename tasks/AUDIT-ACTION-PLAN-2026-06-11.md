# Post-Capture Audit — Reconciled Action Plan (2026-06-11)

Single ordered worklist folding the **101 confirmed findings**
([AUDIT-POST-CAPTURE-backend-2026-06-11.md](AUDIT-POST-CAPTURE-backend-2026-06-11.md) §5)
into the existing REP backlog + new task groups, with the owner OQ rulings applied.

**OQ rulings (2026-06-11):** OQ-1 SX5E→Eurex close (timer shift **sequenced after** the SPX
guard hotfix); OQ-2 ADR-0040 fail-loud **only when zero options return on a first/live run**,
never on recompute, raw persisted **before** filtering; OQ-3 ADR-0028 **enforce** block presence;
OQ-4 ADR-0027 **buffer-and-sort** (deferred — not an active path).

**Ordering = owner plan:** zero-risk fixes → REP0-10 (parallel) → mega-fix. Wave numbers are
*priority*, not strict serialization — fan out non-conflicting work. **Shared tree: claim files
in [TASKBOARD.md](TASKBOARD.md) before editing.** QA-FIX (`fix/live-spine-wiring`) is now merged.

### Progress (2026-06-11)
- ✅ **Wave 1 backend lanes landed** on `audit-fixes-batch1`: STORAGE F-STORE-01/03 (`445d1ac`),
  RISK F-RISK-01/02/03 (`ba9dd26`), BFF F-BFF-01/02 (`059a9e8`). Gate green (983/0/16).
- ✅ **SPX guard hotfix** landed on `hotfix/spx-post-close-guard` (`07c892d`); **F-UNI-01 leap-day
  folded into it** (same `calendar_resolver.py`). Timer-coherence follow-up → `clock-timer-coherence.md`.
- ⚠️ **REP1 = WON'T-FIX**: both micro-swaps break content-hash stability (np.interp 1-ULP drift;
  scipy theilslopes intercept-convention shift). Verdict machine-checked by parity tests. Closed.
- ↪️ **ADR-0040 mega-fix is owned by [`T-raw-invariant.md`](T-raw-invariant.md)** (raw-before-derived
  + persist-entrypoint convergence, OQ-C) — Wave 4 below references it, does not duplicate it.

---

## WAVE 0 — Emergency (handled in a parallel conversation, NOT this track)
- **SPX post-close guard hotfix** — bounded-session window (`next_session_open`) + **raw
  persisted before the post-close filter** + **loud warning on 100% drop**. Deadline tonight
  22:45 CEST. **Blocks the OQ-1 XEUR timer shift** (Wave 5) — do not move that timer first.
  Findings: the new SPX drop bug (sharper than F-IBKR-13), F-ACTOR-01 family.

## WAVE 1 — Quick & safe correctness fixes (no ruling, low break-risk) — START HERE
Each fix pairs with the test the finding names. Two commits.
- **1a — crash / wrong-number-served (do first):**
  - F-BFF-02 — `health.py:40` `row.status` → `qc_status` (runtime AttributeError)
  - F-STORE-01 — `adapter.py:317` restatement-leak one-liner (mirror the correct check at :359)
  - F-BFF-01 — `serializers.py:218-246` remove the double `/100` on Gamma$ (residual after the
    QA-FIX engine fix; fix its hand-built fixture too)
- **1b — numerical / correctness nits:** F-RISK-01/02 (`math.fsum`), F-RISK-03 (split version
  keys), F-UNI-01 (leap-day clamp — also fixes the `_calendar` start bug), F-IBKR-02 (chrono
  expiry sort), F-SCRIPT-01 (loud failed tickers), F-SCRIPT-03 (tie-break), F-ACTOR-07 (dead
  param), F-SNAP-03 (README keyword), F-IBKR-16 (injected sleep).

## WAVE 2 — Documentation-only (zero behaviour risk, batch as few commits)
- **ADR-0041 doc sweep — ONE commit:** F-ORCH-06/07/08/09, F-OPS-04 (5 stale "skip-based
  restart" docstrings/READMEs now contradicted by the overwrite-rerun behaviour).
- **Other drift docs:** F-SCRIPT-02 (bridge-landed README), F-CORE-02/03, F-CONN-02/05/06,
  F-SURF-03/04, F-IBKR-04/10/11, F-UNI-02 (stale `universe.yaml` conid-0 placeholder vs verified
  conids), F-EXEC-02, F-SCRIPT-05, F-ACTOR-06.

## WAVE 3 — REP stack (parallel; fold audit findings into existing rows, do NOT duplicate)
- **[REP0](REP0-dependency-hygiene.md)** ← F-DEP-01 (drop pandas; land-or-drop polars; fix false
  comment).
- ~~**[REP1](archive/REP1-scipy-micro-swaps.md)**~~ — **WON'T-FIX** (both swaps break content-hash stability; see Progress).
- **[REP2](REP2-storage-asof-unification.md)** ← F-ASOF-01, F-STORE-03 (**high care — look-ahead
  boundary**; reuse one DuckDB connection / parity-tested winner).
- **[REP5](REP5-pydantic-bff-contract.md)** ← F-BFF-05 (pydantic `response_model`; coordinate
  `web/src/test/fixtures.ts`). Land before Phase-2 endpoints multiply.
- **[REP6](REP6-pydantic-config-layer.md)** ← F-CORE-01 + **OQ-3 enforce**: missing economic
  block → labeled load error, no silent default. Keep `config_hashes` byte-identical.
- Frontend rows REP3/REP4/REP9/REP10 — outside backend-audit scope (front track).
- REP7/REP8 — **BLOCKED** (need live TradingNode / IBKR live-auth). Leave.

## WAVE 4 — Mega-fix clusters (rulings now in hand; higher break-risk, do SINGLY, gate green)
- **ADR-0040 enforcement — owned by [`T-raw-invariant.md`](T-raw-invariant.md)** (now unblocked,
  QA-FIX merged). Folds F-ACTOR-01 ×7 + F-ACTOR-02/03, F-IBKR-08, F-ORCH-03/04/05, F-QC-02,
  F-BFF-03/04. OQ-2 ratified: capture path fail-loud iff zero options on first run; raw-before-filter;
  EMPTY marker on replay only. **Add F-ACTOR-04 test first**, byte-identity green. **HIGH break-risk,
  hot persistence path.** Update T-raw-invariant's spec with these finding IDs rather than re-spec'ing here.
- **eod_stages refactor** (F-ORCH-02 last-wins outputs + F-EXEC-03 closure side-channels +
  F-ORCH-04 partial-failure flagging) — one coherent rework; return stage products explicitly.
- **Connectivity hardening bundle** (F-IBKR-03/07, F-CONN-04, F-OPS-03) — thread the 429
  budget/backoff from `ibkr_history.yaml`; one shared httpx-retry transport; de-stack the double
  retry.
- **Lookahead-membership** (F-LOOK-01/02) — pass `known_as_of` on backfill + BFF read paths.
- **Collector read-scoping** (F-COLLECT-02/03) — scope `_reload_seen_event_ids` / `build_summary`
  to `trade_date` (kills full-table scans).
- **ADR-0027 buffer-and-sort** (F-COLLECT-01) — **DEFERRED** (OQ-4; not an active prod path).

## Coherence lens (owner principle, applies to every wave)
Adding a new index must stay **coherent, clean, easy, clear** — one registry source, no per-index
hand-set value that can drift from what the calendar/registry derives. Concrete debts this exposes:
- The per-index systemd `.timer` OnCalendar time is **hand-authored** and drifted from
  `session_close` (the whole XEUR/SX5E bug). Fix direction: derive the fire time as a calendar
  safe-upper-bound, or at minimum a test pinning each `.timer` against `resolver.session_close`.
- Prefer registry-iterating tests (e.g. `test_next_session_within_margin` over **every** calendar)
  so a new index is auto-validated and a violation fails loudly, not at capture time.

## WAVE 5 — Operational (some new, some existing tasks)
- **OQ-1 XEUR timer shift** — move `eod-capture@XEUR.timer` to fire after the Eurex close
  (22:00 CEST) + fix the unit comment (it claims a 17:30 cash close). **BLOCKED on Wave 0.**
  Coherent follow-up: pin/derive every index timer against the resolver (see coherence lens).
- **Alert routing** (F-OPS-02) — wire evaluated alerts to Telegram/email
  ([deferred-disconnect-alert](../deferred) memory).
- **ADR-0034 cold-compaction** (F-OPS-05) — existing [daily-bar-compaction](daily-bar-compaction.md).
- **SX5E wrong-instant reflag** — 2026-06-10 SX5E banked data is a 16:15 UTC snapshot mislabeled
  as the 20:00 close; document/migrate under the conformity pass (F-CONF-01/02/03).

## ADRs to write / amend
- **ADR-0040 amendment** (OQ-2) — drives Wave 4 headline.
- **ADR-0028 amendment** (OQ-3) — drives REP6.
- **ADR-0027 clarification** (OQ-4) — deferred.
- **ADR-0039 reinforcement** (F-NB-01) — notebook helper must route through the bridge.

## Leave alone (verified-clean / correctly deferred)
F-EXEC-03 skeleton labels, F-EXEC-04 empty test dirs, F-ACTOR-05 QcInputs overhead.
