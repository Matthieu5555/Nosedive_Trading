from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

ENV_TRANSMIT_ENABLED = "EXECUTION_TRANSMIT_ENABLED"
ENV_SECURITY_REVIEW = "EXECUTION_SECURITY_REVIEW"

MODE_ABSENT = "absent"
MODE_PAPER = "paper"
MODE_LIVE = "live"
_MODES = (MODE_ABSENT, MODE_PAPER, MODE_LIVE)

REVIEW_GREEN = "green"


class GateConfigError(Exception):

    def __init__(self, reason: str, *, field: str, value: object) -> None:
        self.reason = reason
        self.field = field
        self.value = value
        super().__init__(f"{field}={value!r}: {reason}")


@dataclass(frozen=True, slots=True)
class TransmitGate:

    mode: str
    security_review_green: bool

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise GateConfigError(
                "mode must be absent, paper or live", field="mode", value=self.mode
            )


@dataclass(frozen=True, slots=True)
class GateUnparseable:

    reason: str
    detail: str


GateLoad = TransmitGate | GateUnparseable


def load_transmit_gate(env: Mapping[str, str]) -> GateLoad:
    raw_mode = env.get(ENV_TRANSMIT_ENABLED)
    if raw_mode is None or not raw_mode.strip():
        return TransmitGate(mode=MODE_ABSENT, security_review_green=False)

    normalized = raw_mode.strip().lower()
    if normalized in {"false", "0", "no", "off", "paper"}:
        mode = MODE_PAPER
    elif normalized in {"live", "true", "1", "yes", "on"}:
        mode = MODE_LIVE
    else:
        return GateUnparseable(
            reason="unparseable_flag",
            detail=f"{ENV_TRANSMIT_ENABLED}={raw_mode!r} is not a recognized value",
        )

    review = env.get(ENV_SECURITY_REVIEW, "").strip().lower()
    return TransmitGate(mode=mode, security_review_green=review == REVIEW_GREEN)


def load_transmit_gate_from_environment() -> GateLoad:
    return load_transmit_gate(os.environ)
