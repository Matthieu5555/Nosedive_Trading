from __future__ import annotations

import importlib
import pkgutil
from datetime import UTC, date, datetime
from decimal import Decimal

import algotrading.execution as execution
from algotrading.core.provenance import source_ref, stamp
from algotrading.execution import Fill, FillError

_FORBIDDEN = (
    "transmit",
    "place_order",
    "submit_order",
    "send_order",
    "credential",
    "api_key",
    "secret",
    "oauth",
    "broker_client",
)


def _public_names(module: object) -> set[str]:
    return {n for n in dir(module) if not n.startswith("_")}


def test_the_package_exports_no_transmit_or_credential_symbol() -> None:
    offenders = {
        name
        for name in _public_names(execution)
        if any(token in name.lower() for token in _FORBIDDEN)
    }
    assert offenders == set(), f"execution exposes a forbidden symbol: {offenders}"


def test_no_submodule_exposes_a_transmit_or_credential_symbol() -> None:
    for info in pkgutil.walk_packages(execution.__path__, prefix="algotrading.execution."):
        module = importlib.import_module(info.name)
        offenders = {
            name
            for name in _public_names(module)
            if any(token in name.lower() for token in _FORBIDDEN)
        }
        assert offenders == set(), f"{info.name} exposes a forbidden symbol: {offenders}"


def test_fills_are_paper_only_at_the_type_level() -> None:
    ts = datetime(2026, 6, 12, 15, 30, tzinfo=UTC)
    good_stamp = stamp(
        calc_ts=ts,
        code_version="t",
        config_hashes={"execution": "x"},
        source_records=(source_ref("order_tickets", "b", "k"),),
        source_timestamps=(ts,),
    )

    def _build(mode: str) -> Fill:
        return Fill(
            fill_id="f",
            booking_id="b",
            source_basket_id="bsk",
            trade_date=date(2026, 6, 12),
            underlying="SX5E",
            contract_key="SX5E|OPT|C|4400",
            signed_qty=Decimal("1"),
            price=1.0,
            fill_ts=ts,
            provenance=good_stamp,
            mode=mode,
        )

    assert _build("paper").mode == "paper"
    try:
        _build("live")
    except FillError as exc:
        assert exc.field == "mode"
    else:  # pragma: no cover - the pin must hold
        raise AssertionError("a non-paper fill must be unconstructable")
