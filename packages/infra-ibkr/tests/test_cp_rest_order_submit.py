from __future__ import annotations

from datetime import UTC, datetime

import pytest
from algotrading.infra_ibkr.collectors.cp_rest_adapter import (
    CpInstrument,
    CpRestMarketDataAdapter,
)
from algotrading.infra_ibkr.connectivity.cp_rest_order_submit import (
    CpRestOrderSubmit,
    OrderSubmitError,
)

from .conftest import FakeCpTransport

_IK = "OPT:SPY:OPT:20260626:C:758:100:SMART:USD"


def test_submit_posts_to_the_order_endpoint_as_a_separate_verb() -> None:
    transport = FakeCpTransport(
        post_response=[{"order_id": "ORD-1", "order_status": "Submitted"}]
    )
    submit = CpRestOrderSubmit(transport, account_id="DUQ574355")
    ack = submit.submit({"binding_hash": "abc", "legs": []})
    assert transport.post_paths == ["/iserver/account/DUQ574355/orders"]
    assert ack.order_id == "ORD-1"
    assert ack.status == "Submitted"


def test_submit_requires_an_account_id() -> None:
    with pytest.raises(OrderSubmitError):
        CpRestOrderSubmit(FakeCpTransport(), account_id="  ")


def test_submit_rejects_an_unexpected_response_never_silently() -> None:
    transport = FakeCpTransport(post_response={"not": "a list"})
    submit = CpRestOrderSubmit(transport, account_id="DUQ574355")
    with pytest.raises(OrderSubmitError):
        submit.submit({"x": 1})


def test_the_ingestion_adapter_still_never_touches_an_order_path() -> None:
    transport = FakeCpTransport(get_response=[{"conid": 1, "84": "9.27"}])
    adapter = CpRestMarketDataAdapter(
        transport,
        [CpInstrument(instrument_key=_IK, conid=1, underlying="SPY")],
        session_id="ibkr-cp",
        now_fn=lambda: datetime.now(UTC),
        _sleep=lambda _seconds: None,
    )
    adapter.snapshot()
    assert not any(
        "order" in path for path in transport.get_paths + transport.post_paths
    ), "the read-only ingestion adapter must never reach an order endpoint (ADR 0024 §4)"


def test_order_submit_is_not_a_method_on_the_ingestion_adapter() -> None:
    assert not hasattr(CpRestMarketDataAdapter, "submit")
    assert not hasattr(CpRestMarketDataAdapter, "submit_order")
    assert not hasattr(CpRestMarketDataAdapter, "place_order")


def test_submit_is_a_distinct_class_from_the_ingestion_adapter() -> None:
    methods: set[str] = {
        name for name in vars(CpRestMarketDataAdapter) if not name.startswith("_")
    }
    assert "submit" not in methods
