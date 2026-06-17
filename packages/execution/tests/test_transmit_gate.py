from __future__ import annotations

import pytest
from algotrading.execution.transmit.gate import (
    ENV_SECURITY_REVIEW,
    ENV_TRANSMIT_ENABLED,
    MODE_ABSENT,
    MODE_LIVE,
    MODE_PAPER,
    GateConfigError,
    GateUnparseable,
    TransmitGate,
    load_transmit_gate,
)


def test_absent_flag_loads_as_absent_mode_and_review_not_green() -> None:
    gate = load_transmit_gate({})
    assert isinstance(gate, TransmitGate)
    assert gate.mode == MODE_ABSENT
    assert gate.security_review_green is False


def test_blank_flag_is_treated_as_absent() -> None:
    gate = load_transmit_gate({ENV_TRANSMIT_ENABLED: "   "})
    assert isinstance(gate, TransmitGate)
    assert gate.mode == MODE_ABSENT


@pytest.mark.parametrize("value", ["paper", "PAPER", "false", "0", "no", "off"])
def test_paper_synonyms_load_as_paper(value: str) -> None:
    gate = load_transmit_gate({ENV_TRANSMIT_ENABLED: value})
    assert isinstance(gate, TransmitGate)
    assert gate.mode == MODE_PAPER


@pytest.mark.parametrize("value", ["live", "LIVE", "true", "1", "yes", "on"])
def test_live_synonyms_load_as_live(value: str) -> None:
    gate = load_transmit_gate({ENV_TRANSMIT_ENABLED: value})
    assert isinstance(gate, TransmitGate)
    assert gate.mode == MODE_LIVE


@pytest.mark.parametrize("value", ["maybe", "yolo", "liveish", "paperr"])
def test_unrecognized_value_is_unparseable_fail_closed(value: str) -> None:
    gate = load_transmit_gate({ENV_TRANSMIT_ENABLED: value})
    assert isinstance(gate, GateUnparseable)


def test_security_review_green_is_only_the_green_token() -> None:
    for ok in ("green", "GREEN", "  green  "):
        on = load_transmit_gate({ENV_TRANSMIT_ENABLED: "live", ENV_SECURITY_REVIEW: ok})
        assert isinstance(on, TransmitGate) and on.security_review_green is True
    for noise in ("passed", "yes", "1", "amber", ""):
        gate = load_transmit_gate({ENV_TRANSMIT_ENABLED: "live", ENV_SECURITY_REVIEW: noise})
        assert isinstance(gate, TransmitGate) and gate.security_review_green is False


def test_an_unknown_mode_is_rejected_at_construction() -> None:
    with pytest.raises(GateConfigError):
        TransmitGate(mode="elsewhere", security_review_green=False)
