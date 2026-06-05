# Known limitations and support model

This is the honest list of what the platform does *not* do. Reading it now is cheaper
than discovering an assumption in production. The system is a clean kitchen for cooking
volatility analytics; it is deliberately not a trading system, and several of the gaps
below are intentional design boundaries, not unfinished work.

## What it does not do

**It does not place orders.** There is no order-placement path anywhere — no order, no
execution, no position-taking. The connectivity seam reads market data only. The smoke
test asserts this directly: after a full bootstrap, the positions layer is empty because
nothing wrote to it. Positions enter the system as data (the `Position` contract), from a
portfolio source; the platform values and risks them, it does not trade them.

**Nautilus is the runtime spine ([ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md)).**
`nautilus_trader` is a real dependency: its data catalog, replay/backtest engine, and actor host
are the runtime, and our analytics are the pure functions a thin Nautilus `Actor` drives and
stamps provenance onto (`run_analytics` is still a pure function of its inputs). The same actor
runs live and replay via Nautilus's own live==backtest property — the blueprint's single-code-path
mandate. This reverses the interim framework-free stance of ADRs 0007/0020; the pure analytics stay
framework-independent, so they can still be driven from a plain loop if Nautilus ever gets in the
way (the ADR 0016 escape hatch).

**The live IBKR adapter needs an opt-in extra and is not exercised by the test suite.**
There *is* now a concrete live `IbkrBrokerSession` over `ib_async` (ADR 0008), but the
broker SDK is an optional dependency, not installed by default — a live session requires
`uv sync --extra ibkr` and a running Gateway/TWS. The spec still forbids a live IBKR
session in the test suite, so the suite drives the adapter through a fake `ib_async` and
the live socket is proven only by `backend/scripts/ibkr_live_smoke.py`, run by hand. The
default `uv run pytest` runs broker-free against the `FakeBrokerSession` and the disk
`ReplayBrokerSession`. The adapter is read-only: it reads market data and never places an
order. See `backend/src/connectivity/README.md`. (Under
[ADR 0023](../.agent/decisions/0023-nautilus-runtime-spine-and-library-leverage.md), IBKR
connectivity moves to Nautilus's shipped adapter; this hand-rolled `ib_async` `IbkrBrokerSession`
is superseded.)

**Single-broker, single-currency-per-contract assumptions.** The connectivity seam
models one broker session at a time, and the universe resolution requires each contract
to carry its own currency and multiplier (never defaulted) — there is no cross-broker
reconciliation and no FX layer. A contract's currency is a field on its key, not a
portfolio-level conversion.

**No FastAPI service yet.** There is no `app` object and `main.py` is still the `uv init`
hello-world stub (`backend/README.md`). The platform is driven as a library — from the
runbook scripts and the pipeline entrypoints — not served over HTTP. Standing up the
service is later work; the workspace README's `uvicorn main:app` command does not work
today.

**The actor reads the rich in-memory results, not the persisted contracts, within one
run.** The valuation join reads C's rich in-memory objects from the same run (they carry
the discount factor and the QC verdict that the persisted contracts drop). This is
correct and intended, but it means the join is an in-process step within one
`run_analytics` call — you cannot reconstruct a valuation join from persisted partitions
alone; you re-run the day. That is the whole point of one-code-path replay, but it is
worth knowing it is not a separate queryable step.

**Reconstruction needs the raw layer.** Replay and backfill rebuild analytics from
stored raw events. A day with no raw partition comes back `MISSING` and produces nothing
— the system never invents market data to fill a gap. If the raw data was never captured,
it cannot be reconstructed. The raw layer is the one irreplaceable thing; guard it.

**E is behavior-tested, not coverage-gated.** The pure-function core (C and D) carries
the 90% branch-coverage floor; the transport and orchestration tiers (the actor, QC job,
orchestration, reconstruction) are held to named behavior tests instead (ADR 0007,
decision 5). So a green coverage number is a statement about the math core, not about
every line of the operations layer. The operations layer's guarantee is its named tests
(kill-and-restart idempotency, missing-partition flagging, detection-within-interval,
the two headline invariants), not a coverage percentage.

## What it does guarantee

So the limitations are read in context: the four invariants the system *does* enforce
are determinism (same inputs, identical outputs), an immutable raw layer (recompute
everything from the original ticks), provenance on every derived number (which ticks,
which code version, which config hash), and one code path for live and replay. These are
checked, not trusted: the byte-identical replay test and the provenance-verification test
are the headline guarantees. If one of those fails, treat it as the most serious incident
the system can have (see the [incident-response runbook](runbooks/incident-response.md)).

## Support model

When something breaks, the order of operations is: read the failure's named offender,
find the matching row in the [incident-response runbook](runbooks/incident-response.md),
and follow it. The QC plane is built so a failure names the exact maturity, quote,
underlying, or solver — you should rarely be guessing.

Triage severity off the QC escalation level, which is the single definition of the alert
policy:

- **page** — a critical-severity QC fail, or a `collector_death` alert, or a determinism
  divergence (`compare_replay_to_live` disagreeing under one code version, or the
  byte-identical replay test failing). Wake someone.
- **notice** — any non-critical QC fail or any warn, or an `elevated_failure_rate` or
  `missing_partition` alert. Work the triage queue; it is worst-first and names each
  offender.
- **none** — clean. Nothing to do.

Escalation path: an operator handles `notice`-level items from the triage queue and the
runbooks. A `page` goes to the workstream owner of the failing layer — connectivity and
collection issues to B's owner, forward/IV/surface/pricing issues to C's owner, risk and
scenario issues to D's owner, orchestration/QC/replay and any determinism break to E's
owner (Workstream E owns `src/orchestration`, `src/qc`, the actor, and `documentation/`). A
contract change request goes to A's owner, never made in place (see
[interface contracts](interface-contracts.md)). The workstream owners and their branches
are listed in the task files under `tasks/`; the routing table for which directory owns
what is `.agent/map.md`.
