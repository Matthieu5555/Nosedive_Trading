> Source: blueprint PDF, page 45. Faithful transcription — see ../blueprint/README.md for governance status.

# Part XV — Data retention, lineage, and governance

Even a privately operated volatility platform benefits from governance discipline. The most important governance principle is lineage: every derived object should point back to the raw observations, code version, and configuration version that produced it. The second principle is retention: store enough history to support audit, debugging, and replay, but define clear retention tiers so storage costs and operational complexity remain controlled.

### Retention tiers

- Tier 1 — Raw events: highest fidelity; long enough retention to support replay and forensic debugging.
- Tier 2 — Normalized snapshots: retained longer than raw if storage requires compromise, because they are compact and analytically rich.
- Tier 3 — Derived analytics: forwards, IV points, surfaces, risk, and scenarios; retained for long-horizon trend analysis and audit.
- Tier 4 — Summary reports and manifests: retained indefinitely or near-indefinitely because they are small and operationally valuable.

### Lineage requirements

- Every derived record must include source snapshot_ts and source data partition identifiers.
- Every job must emit a manifest with code version and config hashes.
- Any replay or backfill must write a new version identifier instead of silently mutating past results.
- QC outputs should reference both the failing object and the run that generated the object.

### Change-control categories

Not all changes carry the same risk. The team should classify changes into at least three categories. Category A changes alter economics directly, such as pricer logic, solver bounds, surface parameterization, or scenario definitions. Category B changes alter validation or operational behavior, such as thresholds, alert policies, or run schedules. Category C changes are non-economic, such as log-message improvements or documentation fixes. Category A changes should require the strongest regression evidence and the most explicit sign-off.
