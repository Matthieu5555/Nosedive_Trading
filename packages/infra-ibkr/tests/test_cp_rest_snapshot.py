from __future__ import annotations

from typing import Any

import structlog
from algotrading.infra_ibkr.collectors.cp_rest_snapshot import (
    DEFAULT_WARMUP,
    WarmupConfig,
    snapshot_with_warmup,
)
from structlog.testing import capture_logs

from .conftest import FakeCpTransport

_FAST_CONID = 1001
_SLOW_CONID = 2002


def _row(conid: int, *, populated: bool) -> dict[str, Any]:
    if not populated:
        return {"conid": conid}
    return {"conid": conid, "84": "10.0", "86": "10.5"}


def _responder_warm_at(slow_warm_poll: int) -> Any:
    state = {"polls": 0}

    def _respond(_path: str, _params: dict[str, Any]) -> list[dict[str, Any]]:
        state["polls"] += 1
        slow_ready = state["polls"] >= slow_warm_poll
        return [
            _row(_FAST_CONID, populated=True),
            _row(_SLOW_CONID, populated=slow_ready),
        ]

    return _respond


def _no_sleep(_seconds: float) -> None:
    return None


def test_default_warmup_is_more_patient_than_legacy() -> None:
    assert DEFAULT_WARMUP.attempts >= 8
    assert DEFAULT_WARMUP.skip_dead_after is not None
    assert DEFAULT_WARMUP.skip_dead_after > 2


def test_slow_to_warm_conid_is_not_dropped_prematurely() -> None:
    transport = FakeCpTransport(get_responder=_responder_warm_at(slow_warm_poll=6))

    rows = snapshot_with_warmup(
        transport,
        conids=(_FAST_CONID, _SLOW_CONID),
        sleep=_no_sleep,
    )

    populated = {row.conid for row in rows if row.has_market_value()}
    assert populated == {_FAST_CONID, _SLOW_CONID}


def test_legacy_eager_giveup_would_have_dropped_the_slow_conid() -> None:
    legacy = WarmupConfig(attempts=8, skip_dead_after=2)
    transport = FakeCpTransport(get_responder=_responder_warm_at(slow_warm_poll=6))

    rows = snapshot_with_warmup(
        transport,
        conids=(_FAST_CONID, _SLOW_CONID),
        sleep=_no_sleep,
        warmup=legacy,
    )

    populated = {row.conid for row in rows if row.has_market_value()}
    assert _SLOW_CONID not in populated


def test_giveup_logs_warning_with_unpopulated_conids() -> None:
    never = WarmupConfig(attempts=3, skip_dead_after=None)
    transport = FakeCpTransport(get_responder=_responder_warm_at(slow_warm_poll=999))

    with capture_logs() as logs:
        snapshot_with_warmup(
            transport,
            conids=(_FAST_CONID, _SLOW_CONID),
            sleep=_no_sleep,
            warmup=never,
        )

    incomplete = [
        entry for entry in logs if entry["event"] == "ibkr.snapshot.warmup_incomplete"
    ]
    assert len(incomplete) == 1
    entry = incomplete[0]
    assert entry["log_level"] == "warning"
    assert entry["unpopulated_count"] == 1
    assert entry["unpopulated_conids"] == [_SLOW_CONID]
    assert entry["requested_count"] == 2


def test_complete_capture_emits_no_incomplete_warning() -> None:
    structlog.reset_defaults()
    transport = FakeCpTransport(get_responder=_responder_warm_at(slow_warm_poll=1))

    with capture_logs() as logs:
        snapshot_with_warmup(
            transport,
            conids=(_FAST_CONID, _SLOW_CONID),
            sleep=_no_sleep,
        )

    assert not [
        entry for entry in logs if entry["event"] == "ibkr.snapshot.warmup_incomplete"
    ]
