# Ingestion ↔ blueprint conformance audit — 2026-06-15

**Method:** 5 parallel readers (Sonnet), one per ingestion stage, each confronting the code to its
blueprint Part; Opus synthesis. Scope = the ingestion path only (capture → raw → normalize →
lineage → QC/alert). Validated decision embedded: **ingest INDEX options only; constituents =
prices only; ρ̄ from realized vol, not IV** (ADR 0051).

**Bottom line:** the foundation is largely sound — provenance stamps on every derived record, raw
write-path immutability (append-dedup + versioned-write guard), re-run convergence on the `eod_run`
path, session-drop restart coherence. But the blueprint's keystone invariant — *"raw is sufficient
to recompute all downstream analytics"* (`00-overview.md:21`, `01-architecture.md:13`) — has **real
P0 holes**, and nothing on the path **fails loud**. Fix the P0 cluster before the unattended week.

---

## P0 — the raw-completeness + fail-loud cluster (do first)

> **Status update 2026-06-17:** #1 **RESOLVED** by `aca7369` ("raw is faithful — capture every
> observed row; two-sided gate moves to the derived layer", on main) — capture now sequences
> `[*two_sided, *quarantined]` so every observed row reaches raw. #2 + #4 are owned (and #4 newly
> folded) by [infra-raw-invariant](infra-raw-invariant.md), now **un-blocked** (QA-FIX landed).
> #3 = [platform-capture-alert-wiring](platform-capture-alert-wiring.md) (still open, P0).

| # | Finding | Evidence | Fix |
|---|---|---|---|
| 1 | **Quarantined rows never enter raw.** Rows failing the quote-integrity gate are logged then dropped — they never reach `raw_market_events`, so raw cannot reconstruct what IBKR actually returned. The "raw stays immutable" comment is vacuously true (the rows never existed in raw). | `cp_rest_close_capture.py:404-410`; `eod_stages.py:326` feeds only promoted `basket.events` | Write ALL snapshot rows to raw **before** the promotion gate; add a `promoted: bool` column (or a `raw_snapshot_rows` table). Gate filters for *derived*, never for *raw*. |
| 2 | **Raw-before-derived not enforced outside `eod_run`.** `run_incremental_analytics`, `surface_job`, and direct `persist_outputs` can write derived tables into an **empty raw layer** with no error → unreproducible analytics. ADR-0040 invariant is coded only in `reconstruction/batch.py:117`, queued elsewhere. | `eod_stages.py:378`, `jobs.py:280`, `driver.py` persist; `infra-raw-invariant.md` = QUEUED | Shared pre-flight guard `assert raw present for (trade_date)` (raise `RawNotFoundError`) at the top of every analytics entrypoint / `persist_outputs`. This is ADR-0040 with teeth. |
| 3 | **Nothing fails loud / alerts.** A QC-critical (`ESCALATION_PAGE`) result does not force a non-zero exit, so systemd `OnFailure=` never fires; `qc_fail_alert`/`coverage_breach_alerts` are defined but **never called**; the babysitter counts a failed capture as `done` and exits 0; its ALARM goes only to stdout. | `pipeline.py` (no raise on page), `eod_stages.py:_qc`, `eod_babysitter.py:_fire/_babysit/_heartbeat` | = existing **platform-capture-alert-wiring** (P0). Concrete 5-file plan exists (raise `QcEscalationError`; call the alert builders + log ERROR; babysitter tracks `failed`, exits 1; ALARM → `systemd-cat`). |
| 4 | **`persist_outputs` silently skips empty tables** (`if not records: continue`) — "never ran" and "ran, zero output" are indistinguishable. Root cause of the SX5E missing-table incident (ADR-0040 F-ACTOR-01). | `driver.py:1063-1065` | Write a zero-row sentinel / manifest row + warning metric instead of a silent `continue`. |

## P1 — provenance, replay safety, connectivity robustness

| # | Finding | Evidence | Fix |
|---|---|---|---|
| 5 | **Backfill `version=None` silently mutates live analytics in place** — violates "any replay/backfill must write a NEW version id" (`15-data-governance.md:18`). Versioned sub-partitions already supported; just no guard. | `reconstruction/batch.py:93,250` | Make `version` required when `persist=True` (raise if `None`); keep the `persist=False` compare path version-free. |
| 6 | **Job manifest `input_partitions` always empty** — cannot prove which raw partitions fed the analytics (breaks full replay audit). `code_version`/`config_hashes` are fine. | `eod_manifest.py:40` | Populate `input_partitions` with the raw paths actually read per symbol. |
| 7 | **`InstrumentMaster.raw_broker_payload` always `"{}"`** — hollow provenance; the `SecdefInfoRow` is available at the call site but discarded. | `cp_rest_close_capture.py:197-204` | Serialize the `SecdefInfoRow` into `raw_broker_payload` (one-liner). |
| 8 | **Keepalive never started in standalone EOD path** — a cold-cache discovery walk can exceed IBKR's idle timeout; only the babysitter has its own tickle. Session-drop *restart* is coherent (✅), but a mid-walk drop is the risk. | `cp_rest_session.py:154-162` (start exists), `session_factory.py:92,126` (never called) | Call `session.start()`/`stop()` in `build_*_session`; wire `on_drop` to fail fast on 401. |
| 9 | **Timestamp distinction collapsed.** Close-capture pins `exchange_ts = receipt_ts = as_of` and discards the broker's `updated_ms` (used only as a look-ahead guard); normalize fills `exchange_ts = receipt_ts` when no exchange time → the three blueprint timestamps are informationally identical. | `cp_rest_close_capture.py:497,547-548`; `normalize.py:108-109`; `market_fields.py:60` | Persist broker `updated_ms` as `exchange_ts`, keep `as_of` as `receipt_ts`, leave `canonical_ts = as_of` (replay-determinism preserved, observability gained). Don't backfill `exchange_ts` from `receipt_ts` — leave null + a `has_exchange_ts` flag. |

## P2 — conformance cleanups (batch when convenient)

| # | Finding | Evidence | Fix |
|---|---|---|---|
| 10 | Instrument-key canonical field **order** deviates from blueprint (all 9 fields present). | `instrument_key.py:27-37` vs `01-architecture.md:59` | Reorder to match, or document the deviation in the architecture ADR. |
| 11 | Completeness fraction ignores sizes & volume (`bid/ask/last` only) → a size-less snapshot reports `completeness=1.0`. | `snapshots/builder.py:31,198-199` | Add size/volume to `_QUOTE_FIELDS` or a separate `size_completeness`. |
| 12 | `MarketStateSnapshot` exposes only `snapshot_ts` (no exchange/receipt range) — broker timing only auditable via a raw-event join. | `tables.py:64-91` | Add `min/max_exchange_ts` summary fields. |
| 13 | QC outputs link the run (`run_id` ✅) but not the **failing object's** stamp_hash. | `tables.py:606-608` | Add `object_stamp_hash: str \| None` to `QcResult`/`TriageRecord`. |
| 14 | `BrokerTick.provider` defaults to `"DERIBIT"` — scope leftover (Deribit removed, ADR 0042); latent (IBKR path bypasses it). | `normalize.py:58` | Default to `""` / make required. |
| 15 | EOD runbook 3 steps 4-6 (partition finalize, QC report, manifest archive) not automated. | `eod_babysitter.py:182` | Post-capture hooks / `eod_finalize.py`. |

## Already decided / tracked elsewhere (not new work here)

- **ADR 0051 implementation** (remove constituent-option capture, ρ̄ → realized vol): `blueprint-return-dispersion-diagnostic.md` — ⏸ after tonight's close. (Audit cap-F1 re-confirms the lane is still wired in `live_capture.py:134` / `eod_run.py:61`.)
- **#3 alerting** = `platform-capture-alert-wiring.md` (P0, already on the board).
- **#2/#4 raw invariant** = `infra-raw-invariant.md` (was QUEUED/parked — this audit promotes it to P0 and gives it teeth).

## What is CONFORM (no action — recorded so we don't re-audit)

ProvenanceStamp carries source records + timestamps; every derived contract carries `source_snapshot_ts` + `provenance`; raw write-path is append-dedup with a versioned-write guard (`adapter.py:95-161`); `eod_run` re-runs converge; content-addressed raw write makes session-drop restart coherent; reconstruction guards raw-present before derived.
