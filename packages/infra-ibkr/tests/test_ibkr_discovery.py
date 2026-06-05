"""IBKR universe discovery against a fake ib client (skips where ib_async is absent).

Exercises the two-call sequence offline: qualifyContracts -> conId -> reqSecDefOptParams, asserting
the conId is threaded through. The live behaviour is validated on a machine with a Gateway.
"""

import pytest

pytest.importorskip("ib_async")  # the adapter imports ib_async at module load

from datetime import date  # noqa: E402

from algotrading.infra.universe import Underlying, instrument_key  # noqa: E402
from algotrading.infra.universe.discovery import normalize_option_params  # noqa: E402
from algotrading.infra_ibkr.collectors.ibkr_discovery import (  # noqa: E402 — after importorskip
    DiscoveryError,
    IbkrUniverseDiscovery,
)

_SPY = Underlying(symbol="SPY", exchange="SMART", currency="USD", security_type="STK")


class _Qualified:
    def __init__(self, con_id: int) -> None:
        self.conId = con_id


class _Chain:
    exchange = "SMART"
    tradingClass = "SPY"  # noqa: N815 — mirrors the ib_async OptionChain attribute name
    multiplier = "100"
    expirations = {"20260918", "20261218"}
    strikes = {450.0, 460.0}


class _FakeIB:
    def __init__(self, *, qualifies: bool = True) -> None:
        self._qualifies = qualifies
        self.params_args: tuple | None = None

    def qualifyContracts(self, contract):  # noqa: N802 — mirrors ib_async API
        return [_Qualified(123)] if self._qualifies else []

    def reqSecDefOptParams(self, symbol, fop_exchange, sec_type, con_id):  # noqa: N802
        self.params_args = (symbol, fop_exchange, sec_type, con_id)
        return [_Chain()]


def test_fetch_threads_conid_through_to_chain_params():
    ib = _FakeIB()
    params = IbkrUniverseDiscovery(ib).fetch(_SPY)
    assert ib.params_args == ("SPY", "", "STK", 123)  # conId from qualify is threaded through
    assert len(params) == 1
    assert params[0].multiplier == "100"
    assert params[0].expirations == ("20260918", "20261218")  # sorted
    assert params[0].strikes == (450.0, 460.0)


def test_fetch_raises_when_underlying_cannot_be_qualified():
    with pytest.raises(DiscoveryError, match="could not qualify"):
        IbkrUniverseDiscovery(_FakeIB(qualifies=False)).fetch(_SPY)


def test_fetch_returns_empty_when_no_chains_found():
    class _NoChains(_FakeIB):
        def reqSecDefOptParams(self, symbol, fop_exchange, sec_type, con_id):  # noqa: N802
            return []

    assert IbkrUniverseDiscovery(_NoChains()).fetch(_SPY) == ()


def test_fetch_to_canonical_keys_golden():
    """Pin the IBKR leaf -> canonical instrument_key mapping (discover -> normalize -> key)."""
    params = IbkrUniverseDiscovery(_FakeIB()).fetch(_SPY)
    contracts = normalize_option_params(
        params, underlying=_SPY, as_of=date(2026, 1, 1), maturity_window=(1, 400)
    )
    assert sorted(instrument_key(c) for c in contracts) == [
        "OPT:SPY:OPT:20260918:C:450:100:SMART:USD",
        "OPT:SPY:OPT:20260918:C:460:100:SMART:USD",
        "OPT:SPY:OPT:20260918:P:450:100:SMART:USD",
        "OPT:SPY:OPT:20260918:P:460:100:SMART:USD",
        "OPT:SPY:OPT:20261218:C:450:100:SMART:USD",
        "OPT:SPY:OPT:20261218:C:460:100:SMART:USD",
        "OPT:SPY:OPT:20261218:P:450:100:SMART:USD",
        "OPT:SPY:OPT:20261218:P:460:100:SMART:USD",
    ]
