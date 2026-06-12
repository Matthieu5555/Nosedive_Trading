"""The recorded run-state ledger — the operator's memory of what ran and when.

Restart safety and the "identify the last healthy run and the current backlog
instantly" requirement both hang on one durable fact: an append-only record of which
stage finished cleanly for which trade date. This module owns that record. It is a
small JSON ledger under the store root (separate from A's contract tables, because it
is operational bookkeeping, not a typed analytic), written atomically so a crash
mid-write never corrupts it.

Each step of the end-of-day pipeline records a :class:`StageRun` when it completes:
the trade date, the stage name, the outcome, the run id, and the injected timestamp.
The pipeline reads the ledger back on restart to skip stages that already finished
for the date (idempotent resume), and the dashboard reads it to answer "what is the
last healthy run, and what is still outstanding".

Nothing here reads a clock: every timestamp is injected by the caller, the same
discipline the actor and QC plane hold, so a replay of the same pipeline reproduces
the same ledger.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


def _atomic_append_limit() -> int:
    """The largest write() guaranteed atomic against concurrent appenders.

    POSIX guarantees a single ``write()`` of at most ``PIPE_BUF`` bytes is atomic, and
    for a file opened ``O_APPEND`` the kernel also serialises the seek-to-end + write,
    so two concurrent appenders never interleave or overwrite when each record is one
    write of ``<= PIPE_BUF`` bytes. ``select.PIPE_BUF`` is POSIX-only; 512 is its
    portable floor (the standard minimum on every POSIX host) and the fallback on a
    non-POSIX host. Resolved once via this helper so the module has no reassigned
    module-level name (which mypy treats as final on the imported constant).
    """
    try:
        from select import PIPE_BUF
    except ImportError:  # pragma: no cover - non-POSIX hosts only
        return 512
    return PIPE_BUF


_PIPE_BUF = _atomic_append_limit()

# The five canonical end-of-day stages, in run order (Part IV.F). A stage name is the
# stable key the ledger, dashboard, and pipeline all agree on, so it lives once here.
STAGE_UNIVERSE_REFRESH = "universe_refresh"
STAGE_COLLECTION = "collection"
STAGE_ANALYTICS = "analytics"
STAGE_RECONCILIATION = "reconciliation"
STAGE_QC = "qc"

EOD_STAGES: tuple[str, ...] = (
    STAGE_UNIVERSE_REFRESH,
    STAGE_COLLECTION,
    STAGE_ANALYTICS,
    STAGE_RECONCILIATION,
    STAGE_QC,
)

# A stage outcome. "ok" means the stage finished cleanly; "failed" means it ran but
# its work did not pass (a QC fail, a reconciliation breach). A stage that raised
# never records — the absence of a record is exactly what marks it as backlog.
OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"

_LEDGER_FILENAME = "_run_state.jsonl"


class LedgerLineTooLargeError(ValueError):
    """A ledger record encodes to more bytes than an atomic append can guarantee.

    The lock-free append relies on a single ``write()`` of at most ``PIPE_BUF`` bytes
    being atomic against concurrent appenders. A line larger than that could interleave
    with another writer and tear the ledger, so we refuse it loudly rather than risk a
    silent corruption — carrying the offending size so the caller can see the breach.
    """

    def __init__(self, line_bytes: int, limit: int) -> None:
        self.line_bytes = line_bytes
        self.limit = limit
        super().__init__(
            f"ledger record is {line_bytes} bytes, exceeds the {limit}-byte atomic "
            f"append limit (PIPE_BUF); shorten run_id/stage to keep the append atomic"
        )


@dataclass(frozen=True, slots=True)
class StageRun:
    """One recorded completion of one pipeline stage for one trade date."""

    trade_date: date
    stage: str
    outcome: str
    run_id: str
    recorded_ts: datetime

    @property
    def is_ok(self) -> bool:
        """True when the stage finished cleanly."""
        return self.outcome == OUTCOME_OK


def _ledger_path(root: Path) -> Path:
    return Path(root) / _LEDGER_FILENAME


def _encode(stage_run: StageRun) -> str:
    return json.dumps(
        {
            "trade_date": stage_run.trade_date.isoformat(),
            "stage": stage_run.stage,
            "outcome": stage_run.outcome,
            "run_id": stage_run.run_id,
            "recorded_ts": stage_run.recorded_ts.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode(line: str) -> StageRun:
    payload = json.loads(line)
    return StageRun(
        trade_date=date.fromisoformat(payload["trade_date"]),
        stage=payload["stage"],
        outcome=payload["outcome"],
        run_id=payload["run_id"],
        recorded_ts=datetime.fromisoformat(payload["recorded_ts"]),
    )


def record_stage(root: Path, stage_run: StageRun) -> None:
    """Append one stage completion to the ledger, concurrency-safely.

    The ledger is JSON-lines: one record per line, append-only. The append is a single
    ``os.write`` of one ``\\n``-terminated JSON line to a file opened ``O_APPEND``. On
    POSIX the kernel serialises the implicit seek-to-end and the write for ``O_APPEND``,
    and a write of at most ``PIPE_BUF`` bytes is atomic, so two processes appending
    concurrently never interleave or overwrite each other — both records survive. This
    replaces the earlier read-modify-rename, which let two writers read the same file,
    each append, and have the last rename silently drop the other's record (the two
    templated EOD timers share one ledger and a ``Persistent=true`` catch-up fires them
    near-simultaneously). Recording the same stage twice (a re-run of an
    already-finished stage) simply appends a second row; readers take the latest per
    (trade_date, stage), so a duplicate is harmless rather than a conflict.

    Raises :class:`LedgerLineTooLargeError` if the encoded record would exceed the
    atomic-append size, rather than risk a torn write.
    """
    line = (_encode(stage_run) + "\n").encode("utf-8")
    if len(line) > _PIPE_BUF:
        raise LedgerLineTooLargeError(len(line), _PIPE_BUF)
    path = _ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line)
    finally:
        os.close(fd)


def read_stage_runs(root: Path) -> list[StageRun]:
    """Read every recorded stage run, oldest first. Empty when nothing has run."""
    path = _ledger_path(root)
    if not path.exists():
        return []
    runs: list[StageRun] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            runs.append(_decode(line))
    return runs


def latest_by_stage(runs: Sequence[StageRun], trade_date: date) -> dict[str, StageRun]:
    """The most recent recorded run for each stage on one trade date.

    A stage re-run after a fix appends a fresh row, so "did this stage finish, and how"
    is answered by the last row for it — which is what this returns, keyed by stage.
    """
    latest: dict[str, StageRun] = {}
    for run in runs:
        if run.trade_date == trade_date:
            latest[run.stage] = run
    return latest


def completed_stages(root: Path, trade_date: date) -> set[str]:
    """The stages that finished cleanly (outcome ok) for a trade date.

    Observability, not a gate: the pipeline re-runs every stage on a re-fire
    (overwrite-by-re-run, ADR 0032 refined) and only *logs* this set at start. It is
    the dashboard's health key — :func:`backlog_stages` and
    :func:`last_healthy_trade_date` derive from it. A stage whose latest row recorded
    a ``failed`` outcome is *not* completed.
    """
    latest = latest_by_stage(read_stage_runs(root), trade_date)
    return {stage for stage, run in latest.items() if run.is_ok}


def backlog_stages(root: Path, trade_date: date) -> list[str]:
    """The canonical stages not yet finished cleanly for a trade date, in run order.

    The operator's "current backlog" for the date: every EOD stage that has not
    recorded a clean completion, listed in the order the pipeline would run them.
    """
    done = completed_stages(root, trade_date)
    return [stage for stage in EOD_STAGES if stage not in done]


def last_healthy_trade_date(root: Path) -> date | None:
    """The most recent trade date whose full EOD sequence finished cleanly.

    The operator's "last healthy run": the latest date for which every canonical
    stage recorded a clean completion. ``None`` when no date is fully clean yet.
    """
    runs = read_stage_runs(root)
    dates = sorted({run.trade_date for run in runs}, reverse=True)
    for trade_date in dates:
        if not backlog_stages(root, trade_date):
            return trade_date
    return None
