# Operations docs

This is the operations handbook for the volatility platform — the layer that turns
the green code under `backend/src` into something a person can run, watch, and fix
without the original authors. Everything here describes what the code does today, not
what it is meant to do; if a command here stops working, the end-to-end test
`backend/tests/test_handover_e2e.py` is meant to go red and force this doc back into
line.

Start with the runbook for the task in front of you. The five live in
[`runbooks/`](runbooks/): [start of day](runbooks/start-of-day.md),
[intraday health](runbooks/intraday-health.md), [end of day](runbooks/end-of-day.md),
[replay/backfill](runbooks/replay-backfill.md), and
[incident response](runbooks/incident-response.md). When something is broken, go
straight to incident response — it routes you to the right QC check and the right
runbook.

The other three documents are reference, not procedure.
[`interface-contracts.md`](interface-contracts.md) is the frozen list of the typed
objects that cross a workstream line and the rule for changing one.
[`release-management.md`](release-management.md) is the rule that every
economics-affecting change ships a release artifact, plus the template to fill in.
[`known-limitations.md`](known-limitations.md) is the honest list of what the system
does *not* do, and who to contact when something breaks.

## New engineer, 30 minutes

This is the path the acceptance test drives. Do it once on a fresh checkout and you
will understand the shape of the whole system. Every command below is real; the test
`backend/tests/test_handover_e2e.py` runs the same sequence and asserts an artifact at
each step.

First, set up the environment. The backend uses `uv` for everything; never pip or
conda.

```
cd backend
uv sync
```

Confirm the gate is green before you touch anything — this is the same gate every
change must pass.

```
uv run ruff check . && uv run mypy . && uv run pytest -q
```

Second, run the connectivity smoke test. It is the bootstrap the start-of-day runbook
opens with: resolve one contract off a (fake) broker session, capture one quote, write
it to disk, and place no orders.

```
uv run pytest tests/test_smoke_bootstrap.py -q
```

Third, trigger a replay. Reconstruction replays a stored raw day through the *same*
actor compute path as live and writes the derived analytics. The replay/backfill
runbook has the full script; the one-line proof that it works is:

```
uv run pytest tests/test_replay_byte_identical.py -q
```

That test runs `actor.run_analytics` over a live event stream and `actor.run_day`
over the same events replayed off disk, then asserts two things: the returned
`ActorOutputs` are equal (a `==` over frozen dataclasses) and the persisted Parquet
partitions are byte-for-byte identical. The new-engineer "trigger a replay" step
above uses `reconstruction.reconstruct_day` (the date-range driver over the same
compute path); both pin the same property the whole architecture exists to make
true.

Fourth, read a QC report. The QC plane is a library of ten named checks; the
orchestration layer's `run_qc` runs them over a day's outputs and rolls a report whose
triage table names the exact failing object. See the
[end-of-day runbook](runbooks/end-of-day.md) for how to generate one, and the
[QC README](../backend/src/qc/README.md) for the ten checks and the triage/escalation
rules.

Fifth — the question the acceptance criterion ends on — know where to investigate a
failed surface build. A surface is built per maturity from that maturity's IV points,
which come from the forward, which comes from the snapshot. So you walk the chain
*backwards* from the symptom: the `check_surface_fit_error` QC result names the failing
`underlying` + `maturity`; from there check `check_iv_solver_convergence` (did the
solves converge for that maturity?), then `check_forward_stability` and
`check_parity_residual` (was the forward recoverable?), then
`check_underlying_quote_health` and `check_collector_continuity` (was the input data
even there?). The [incident-response runbook](runbooks/incident-response.md) lays this
walk out as a table. The code lives in `backend/src/surfaces`, `backend/src/iv`,
`backend/src/forwards`, and `backend/src/snapshots`; the actor that wires them is
`backend/src/actor`.

## Where the truth lives

These docs are the operations layer. The authority on *what each module does* is the
README next to the code (`backend/src/<module>/README.md`) and the typed contracts in
`backend/src/contracts`. For convenience those per-module READMEs are also reachable
from one roof here: [`modules/`](modules/) holds a symlink to each one, so the same
single file lives next to its code *and* under `documentation/` — there is no second
copy to drift. The authority on *why* a non-obvious choice was made is the ADRs in
`.agent/decisions/`. When this doc and a code README disagree, the code README wins and
this doc is the bug.
