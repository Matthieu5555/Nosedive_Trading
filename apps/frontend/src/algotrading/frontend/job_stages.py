from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


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
