from __future__ import annotations

import importlib.util

import pytest
from algotrading.infra_ibkr.connectivity.nautilus_ibkr import (
    IbkrExtraNotInstalled,
    build_data_client_config,
)

_HAS_IBKR_EXTRA = importlib.util.find_spec("ibapi") is not None


@pytest.mark.skipif(_HAS_IBKR_EXTRA, reason="ibkr extra present: the guard cannot trigger")
def test_builder_raises_actionable_error_without_extra() -> None:
    with pytest.raises(IbkrExtraNotInstalled) as excinfo:
        build_data_client_config(port=4002, client_id=7)
    assert "uv sync --extra ibkr" in str(excinfo.value)


@pytest.mark.skipif(not _HAS_IBKR_EXTRA, reason="ibkr extra absent (no Gateway in CI)")
def test_builder_constructs_config_with_extra() -> None:
    config = build_data_client_config(
        host="127.0.0.1",
        port=4002,
        client_id=7,
        delayed=True,
        load_instrument_ids=("SPY.SMART",),
    )
    assert config.ibg_host == "127.0.0.1"
    assert config.ibg_port == 4002
    assert config.ibg_client_id == 7
