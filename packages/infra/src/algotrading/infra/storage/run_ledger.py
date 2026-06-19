"""Minimal, read-only view of the run-state ledger for the storage layer.

The end-of-day pipeline appends one JSONL row per stage per capture to
``<store-root>/_run_state.jsonl`` (see
``algotrading.infra.orchestration.run_state``). That file physically lives under
the parquet store root, so the store can read it to resolve a capture identity
(``run_id``) without depending on the orchestration layer above it.

This module deliberately reads only the fields it needs (``trade_date``,
``run_id``, ``recorded_ts``) and never writes. The authoritative writer and the
richer ``StageRun`` model stay in ``orchestration.run_state``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

_LEDGER_FILENAME = "_run_state.jsonl"


def latest_run_id_for(root: Path, trade_date: date) -> str | None:
    """The ``run_id`` of the most recent capture banked for ``trade_date`` in the
    ledger under ``root``, or ``None`` when no run is recorded.

    "Most recent" is by the latest ``recorded_ts`` across the run's stage rows,
    which mirrors how the serving view picks the canonical close. This is the run
    whose data the store currently holds (overwrite-last-wins).
    """
    path = Path(root) / _LEDGER_FILENAME
    if not path.exists():
        return None
    target = trade_date.isoformat()
    latest_ts: dict[str, datetime] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("trade_date") != target:
            continue
        run_id = payload.get("run_id")
        recorded = payload.get("recorded_ts")
        if run_id is None or recorded is None:
            continue
        ts = datetime.fromisoformat(recorded)
        current = latest_ts.get(run_id)
        if current is None or ts > current:
            latest_ts[run_id] = ts
    if not latest_ts:
        return None
    return max(latest_ts, key=lambda rid: latest_ts[rid])
