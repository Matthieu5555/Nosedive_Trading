from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from algotrading.infra.orchestration.run_state import EOD_STAGES


class SampleStage(StrEnum):
    RESOLVE = "resolve"
    COLLECT = "collect"
    FIT = "fit"
    SUMMARIZE = "summarize"


@dataclass(frozen=True, slots=True)
class StageLabel:

    stage: SampleStage
    label: str


_SAMPLE_SEQUENCE: tuple[StageLabel, ...] = (
    StageLabel(SampleStage.RESOLVE, "Finding the last captured day"),
    StageLabel(SampleStage.COLLECT, "Collecting the options chain"),
    StageLabel(SampleStage.FIT, "Fitting the surface"),
    StageLabel(SampleStage.SUMMARIZE, "Surface summary"),
)

_SAMPLE_INDEX: dict[SampleStage, int] = {
    entry.stage: position for position, entry in enumerate(_SAMPLE_SEQUENCE, start=1)
}

_SAMPLE_LABEL: dict[SampleStage, str] = {
    entry.stage: entry.label for entry in _SAMPLE_SEQUENCE
}

SAMPLE_STAGE_TOTAL: int = len(_SAMPLE_SEQUENCE)


def sample_stage_index(stage: SampleStage) -> int:
    return _SAMPLE_INDEX[stage]


def sample_stage_label(stage: SampleStage) -> str:
    return _SAMPLE_LABEL[stage]


# Real close-capture progress. The capture subprocess (scripts/eod_run.py) drives the five EOD
# pipeline stages and logs `orchestration.eod.stage.start {stage}` as each begins; we map those raw
# stage names to a 1-based position and a PM-legible label so the Operations job row shows a live
# step tracker, the same widget the SAMPLE replay uses. Keyed off EOD_STAGES so the order can never
# drift from the pipeline.
_CAPTURE_LABELS: dict[str, str] = {
    "universe_refresh": "Refreshing the index universe",
    "collection": "Capturing the option chains",
    "analytics": "Building surfaces and analytics",
    "reconciliation": "Reconciling positions",
    "qc": "Quality control",
}

_CAPTURE_INDEX: dict[str, int] = {
    name: position for position, name in enumerate(EOD_STAGES, start=1)
}

CAPTURE_STAGE_TOTAL: int = len(EOD_STAGES)


def capture_stage_index(stage_name: str) -> int | None:
    return _CAPTURE_INDEX.get(stage_name)


def capture_stage_label(stage_name: str) -> str:
    return _CAPTURE_LABELS.get(stage_name, stage_name)
