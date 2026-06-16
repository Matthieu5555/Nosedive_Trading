> Source: blueprint PDF, pages 31–32. Faithful transcription — see ../blueprint/README.md for governance status.

# Part VI — Operational runbooks

## Runbook 1 — Start of day

Runbook 1 — Start of day should be written as a checklist that an operator can follow during a live session. Below is the minimum recommended content.

1. Verify the scheduler, collector host, and storage endpoints are healthy before market open.
2. Check that the IBKR session is authenticated and receiving heartbeats.
3. Confirm the instrument master for the day has been refreshed or explicitly waived.
4. Confirm that market-data entitlements are active for the monitored products.
5. Open the dashboard and verify zero backlog in the raw-event writer.
6. Run the bootstrap smoke test for one underlying and one option contract.
7. Document any known degraded mode before the session begins.

## Runbook 2 — Intraday health monitoring

Runbook 2 — Intraday health monitoring should be written as a checklist that an operator can follow during a live session. Below is the minimum recommended content.

1. Watch event-rate dashboards for both underlyings and options.
2. Monitor the stale-quote ratio and collector reconnect count.
3. Inspect the latest forward-confidence score distribution.
4. Review solver convergence ratio and fit-error metrics for the active underlyings.
5. Check that the latest scenario report completed on schedule.
6. Escalate if any QC fail persists beyond the configured tolerance window.

## Runbook 3 — End of day

Runbook 3 — End of day should be written as a checklist that an operator can follow during a live session. Below is the minimum recommended content.

1. Ensure all collector sessions closed cleanly and raw partitions are finalized.
2. Run end-of-day snapshot reconciliation and surface rebuild if required.
3. Publish final risk and scenario reports for the day.
4. Generate and archive the QC report and triage table.
5. Record run identifiers, code version, config hashes, and operator notes.
6. Back up or replicate the day's artifacts according to retention policy.

## Runbook 4 — Replay / backfill

Runbook 4 — Replay / backfill should be written as a checklist that an operator can follow during a live session. Below is the minimum recommended content.

1. Select the date range and verify raw partitions exist for every date.
2. Pin the code version and configuration version used for replay.
3. Run snapshot rebuild, forward build, IV solve, surface build, risk, and QC in sequence.
4. Write outputs to versioned historical partitions rather than overwriting prior analytics.
5. Compare replay diagnostics with any live outputs available for the same dates.
6. Archive the replay manifest and summary statistics.

## Runbook 5 — Incident response

Runbook 5 — Incident response should be written as a checklist that an operator can follow during a live session. Below is the minimum recommended content.

1. Acknowledge the alert and identify whether the incident is in connectivity, data, analytics, or orchestration.
2. Check the latest healthy heartbeat and the last successful write to each critical table.
3. Collect logs around the failure window using the run and correlation identifiers.
4. Perform the documented restart procedure for the affected service only.
5. If data loss is suspected, quarantine downstream jobs until replay or gap-filling can be performed.
6. Write a short incident note capturing impact, root cause, remediation, and follow-up actions.

## Operator escalation policy

Not every QC warning requires immediate intervention. The escalation policy should distinguish between informational warnings, action-required warnings, and hard failures. For example, a small rise in fit error may warrant observation, while a collector outage or a missing scenario report is a hard failure requiring intervention. Define severity levels up front and map each to an expected response time and owner.

- Severity 1: data collection halted or storage unavailable. Immediate intervention required.
- Severity 2: analytics incomplete or QC hard failure on a monitored underlying. Same-session intervention required.
- Severity 3: quality degradation within tolerances but trending worse. Investigate and track.
- Severity 4: informational events and low-risk anomalies. Log and review later.
