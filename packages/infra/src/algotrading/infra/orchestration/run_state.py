from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


def _atomic_append_limit() -> int:
    try:
        from select import PIPE_BUF
    except ImportError:  # pragma: no cover - non-POSIX hosts only
        return 512
    return PIPE_BUF


_PIPE_BUF = _atomic_append_limit()

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

OUTCOME_OK = "ok"
OUTCOME_FAILED = "failed"

_LEDGER_FILENAME = "_run_state.jsonl"


class LedgerLineTooLargeError(ValueError):

    def __init__(self, line_bytes: int, limit: int) -> None:
        self.line_bytes = line_bytes
        self.limit = limit
        super().__init__(
            f"ledger record is {line_bytes} bytes, exceeds the {limit}-byte atomic "
            f"append limit (PIPE_BUF); shorten run_id/stage to keep the append atomic"
        )


@dataclass(frozen=True, slots=True)
class StageRun:

    trade_date: date
    stage: str
    outcome: str
    run_id: str
    recorded_ts: datetime

    @property
    def is_ok(self) -> bool:
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
    path = _ledger_path(root)
    if not path.exists():
        return []
    runs: list[StageRun] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            runs.append(_decode(line))
    return runs


def latest_by_stage(runs: Sequence[StageRun], trade_date: date) -> dict[str, StageRun]:
    latest: dict[str, StageRun] = {}
    for run in runs:
        if run.trade_date == trade_date:
            latest[run.stage] = run
    return latest


def completed_stages(root: Path, trade_date: date) -> set[str]:
    latest = latest_by_stage(read_stage_runs(root), trade_date)
    return {stage for stage, run in latest.items() if run.is_ok}


def backlog_stages(root: Path, trade_date: date) -> list[str]:
    done = completed_stages(root, trade_date)
    return [stage for stage in EOD_STAGES if stage not in done]


@dataclass(frozen=True, slots=True)
class CaptureRun:
    """One addressable capture for a ``trade_date``: its ``run_id`` (the receipt
    identity) and the latest stage timestamp banked for that run."""

    run_id: str
    recorded_ts: datetime


def runs_for(root: Path, trade_date: date) -> list[CaptureRun]:
    """Every distinct capture banked for ``trade_date``, newest first.

    Each capture is keyed by its ``run_id`` (the receipt identity emitted by the
    pipeline as ``correlation_id``). Two same-day re-fetches are two ``CaptureRun``s
    here even though the parquet store keeps only the latest run's *data*
    (overwrite-last-wins). This is the identity that lets a reader address a
    specific capture; resolving it to data is the caller's job (see
    ``ParquetStore.read(run_id=...)``). ``recorded_ts`` is the latest stage time
    for the run.
    """
    latest_ts: dict[str, datetime] = {}
    for run in read_stage_runs(root):
        if run.trade_date != trade_date:
            continue
        current = latest_ts.get(run.run_id)
        if current is None or run.recorded_ts > current:
            latest_ts[run.run_id] = run.recorded_ts
    runs = [CaptureRun(run_id=rid, recorded_ts=ts) for rid, ts in latest_ts.items()]
    runs.sort(key=lambda r: r.recorded_ts, reverse=True)
    return runs


def latest_run_id(root: Path, trade_date: date) -> str | None:
    """The ``run_id`` of the most recent capture for ``trade_date`` (the one whose
    data the store currently serves), or ``None`` if no run is banked."""
    runs = runs_for(root, trade_date)
    return runs[0].run_id if runs else None


def last_healthy_trade_date(root: Path) -> date | None:
    runs = read_stage_runs(root)
    dates = sorted({run.trade_date for run in runs}, reverse=True)
    for trade_date in dates:
        if not backlog_stages(root, trade_date):
            return trade_date
    return None
