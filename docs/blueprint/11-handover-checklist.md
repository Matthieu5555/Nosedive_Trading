> Source: blueprint PDF, pages 36–37. Faithful transcription — see ../blueprint/README.md for governance status.

# Part XI — Junior handover checklist

The final section is intentionally practical. A junior implementer or operator should be able to check off each item and demonstrate competence without needing any hidden context about the strategy that will eventually consume the analytics.

1. Can explain the difference between raw events, snapshots, and derived analytics.
2. Can run the connectivity smoke test and interpret failures.
3. Can refresh the instrument master and inspect a discovered option chain.
4. Can locate the latest raw partition and the latest surface partition for a chosen underlying.
5. Can explain how the forward for one maturity was chosen and where the diagnostics are stored.
6. Can rerun the IV solver for one contract from a saved snapshot.
7. Can plot accepted IV points and fitted surface slice for one maturity.
8. Can interpret line-level Greeks and aggregate risk tables.
9. Can run the scenario report and identify the worst-case scenario and contributors.
10. Can read the QC report and navigate to the triage table for a failed maturity.
11. Can restart a failed collector without corrupting the raw store.
12. Can launch a historical replay for a chosen date range and compare outputs with prior versions.
