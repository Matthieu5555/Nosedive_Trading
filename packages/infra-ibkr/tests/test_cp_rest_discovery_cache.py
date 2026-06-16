from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from algotrading.infra.storage.adapter import ParquetStore
from algotrading.infra.universe import ChainSelection
from algotrading.infra_ibkr.collectors.cp_rest_close_capture import collect_live_basket
from algotrading.infra_ibkr.collectors.cp_rest_discovery_cache import (
    DEFAULT_MAX_AGE_DAYS,
    TRSRV_SECDEF_BATCH,
    DiscoveryCache,
    revalidate_conids,
)
from algotrading.infra_ibkr.collectors.cp_rest_snapshot import (
    WarmupConfig,
    snapshot_with_warmup,
)

from .test_cp_rest_close_capture import (
    _MONTHS,
    _STRIKES,
    CLOSE,
    NEXT_OPEN,
    SPX,
    _config,
    _conid_for,
    _expected_option_keys,
    _FakeGateway,
)

_SELECTION = ChainSelection(max_expiries=2, min_strikes_per_side=3, option_exchange="CBOE")
_CAPTURE_DATE = CLOSE.date()


def _expected_token_to_conid() -> dict[str, str]:
    out: dict[str, str] = {}
    for expiry in _MONTHS.values():
        for strike in _STRIKES:
            for right in ("C", "P"):
                token = f"{expiry.isoformat()}|{strike:.10g}|{right}"
                out[token] = str(_conid_for(expiry, strike, right))
    return out


def _cache(tmp_path: Any, **kwargs: Any) -> DiscoveryCache:
    return DiscoveryCache(ParquetStore(tmp_path), **kwargs)


def _store_known_chain(cache: DiscoveryCache, *, as_of: date) -> dict[str, str]:
    token_to_conid = _expected_token_to_conid()
    expirations = tuple(sorted(e.strftime("%Y%m%d") for e in _MONTHS.values()))
    cache.store_chain(
        underlying="SPX",
        as_of=as_of,
        exchange="CBOE",
        multiplier="100",
        months=tuple(_MONTHS),
        expirations=expirations,
        strikes=tuple(sorted(_STRIKES)),
        conid_by_contract=token_to_conid,
    )
    return token_to_conid


def test_store_then_load_round_trips_the_chain_and_conid_map(tmp_path: Any) -> None:
    cache = _cache(tmp_path)
    expected = _store_known_chain(cache, as_of=_CAPTURE_DATE)

    loaded = cache.load(underlying="SPX", capture_date=_CAPTURE_DATE)
    assert loaded is not None
    assert dict(loaded.conid_by_contract) == expected
    assert set(loaded.expirations) == {e.strftime("%Y%m%d") for e in _MONTHS.values()}
    assert set(loaded.strikes) == set(_STRIKES)
    assert loaded.multiplier == "100"
    assert loaded.conids == tuple(sorted(int(c) for c in expected.values()))


def test_load_misses_for_an_unknown_underlying(tmp_path: Any) -> None:
    cache = _cache(tmp_path)
    _store_known_chain(cache, as_of=_CAPTURE_DATE)
    assert cache.load(underlying="UNSEEN", capture_date=_CAPTURE_DATE) is None


@pytest.mark.parametrize(
    ("age_days", "fresh"),
    [
        (0, True),
        (DEFAULT_MAX_AGE_DAYS, True),
        (DEFAULT_MAX_AGE_DAYS + 1, False),
        (-1, False),
    ],
)
def test_fresh_for_window_edges(tmp_path: Any, age_days: int, fresh: bool) -> None:
    cache = _cache(tmp_path)
    as_of = date(2026, 3, 12)
    capture = date.fromordinal(as_of.toordinal() + age_days)
    assert cache.fresh_for(as_of_date=as_of, capture_date=capture) is fresh


def test_load_returns_none_when_only_entry_is_stale(tmp_path: Any) -> None:
    cache = _cache(tmp_path, max_age_days=2)
    as_of = date(2026, 3, 1)
    _store_known_chain(cache, as_of=as_of)
    capture = date.fromordinal(as_of.toordinal() + 3)
    assert cache.load(underlying="SPX", capture_date=capture) is None
    assert cache.load(underlying="SPX", capture_date=date.fromordinal(as_of.toordinal() + 2))


def test_load_picks_the_freshest_non_stale_row(tmp_path: Any) -> None:
    cache = _cache(tmp_path, max_age_days=30)
    older = date(2026, 3, 1)
    newer = date(2026, 3, 10)
    _store_known_chain(cache, as_of=older)
    cache.store_chain(
        underlying="SPX",
        as_of=newer,
        exchange="CBOE",
        multiplier="100",
        months=("JUN26",),
        expirations=(_MONTHS["JUN26"].strftime("%Y%m%d"),),
        strikes=(100.0,),
        conid_by_contract={
            f"{_MONTHS['JUN26'].isoformat()}|100|C": str(_conid_for(_MONTHS["JUN26"], 100.0, "C"))
        },
    )
    loaded = cache.load(underlying="SPX", capture_date=date(2026, 3, 12))
    assert loaded is not None
    assert loaded.as_of_date == newer
    assert len(loaded.conid_by_contract) == 1


def _discovery_walk_paths(gateway: _FakeGateway) -> list[str]:
    return [
        path
        for path, _ in gateway.calls
        if path in ("/iserver/secdef/strikes", "/iserver/secdef/info")
    ]


def test_warm_cache_capture_makes_zero_secdef_calls(tmp_path: Any) -> None:
    cache = _cache(tmp_path)
    _store_known_chain(cache, as_of=_CAPTURE_DATE)

    gateway = _FakeGateway()
    basket = collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=_SELECTION, discovery_cache=cache,
    )
    assert basket is not None
    option_keys = {
        (k.expiry, k.strike, k.option_right) for k in basket.instruments if k.is_option()
    }
    assert option_keys == _expected_option_keys()
    assert _discovery_walk_paths(gateway) == []
    assert any(p == "/iserver/marketdata/snapshot" for p, _ in gateway.calls)


def test_cold_cache_falls_back_to_live_discovery_then_warms(tmp_path: Any) -> None:
    cache = _cache(tmp_path)

    cold_gateway = _FakeGateway()
    first = collect_live_basket(
        cold_gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=_SELECTION, discovery_cache=cache,
    )
    assert first is not None
    assert any(p == "/iserver/secdef/info" for p in _discovery_walk_paths(cold_gateway))

    loaded = cache.load(underlying="SPX", capture_date=_CAPTURE_DATE)
    assert loaded is not None
    assert dict(loaded.conid_by_contract) == _expected_token_to_conid()

    warm_gateway = _FakeGateway()
    second = collect_live_basket(
        warm_gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=_SELECTION, discovery_cache=cache,
    )
    assert second is not None
    assert "/iserver/secdef/info" not in _discovery_walk_paths(warm_gateway)


def test_stale_cache_triggers_a_live_rediscovery(tmp_path: Any) -> None:
    cache = _cache(tmp_path, max_age_days=2)
    _store_known_chain(cache, as_of=date.fromordinal(_CAPTURE_DATE.toordinal() - 10))

    gateway = _FakeGateway()
    basket = collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=_SELECTION, discovery_cache=cache,
    )
    assert basket is not None
    assert any(p == "/iserver/secdef/info" for p in _discovery_walk_paths(gateway))


class _RevalidateTransport:

    def __init__(self, *, delisted: set[int] | None = None) -> None:
        self.batches: list[list[int]] = []
        self._delisted = delisted or set()

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert path == "/trsrv/secdef"
        conids = [int(c) for c in str((params or {})["conids"]).split(",")]
        self.batches.append(conids)
        return {"secdef": [{"conid": c} for c in conids if c not in self._delisted]}


def test_revalidate_batches_at_200(tmp_path: Any) -> None:
    conids = list(range(1000, 1450))
    transport = _RevalidateTransport()
    valid = revalidate_conids(transport, conids)

    assert [len(b) for b in transport.batches] == [TRSRV_SECDEF_BATCH, TRSRV_SECDEF_BATCH, 50]
    flattened = [c for batch in transport.batches for c in batch]
    assert sorted(flattened) == conids
    assert valid == frozenset(conids)


def test_revalidate_drops_a_delisted_conid(tmp_path: Any) -> None:
    conids = [10, 20, 30]
    transport = _RevalidateTransport(delisted={20})
    valid = revalidate_conids(transport, conids)
    assert valid == frozenset({10, 30})


def test_capture_with_revalidation_drops_a_delisted_contract(tmp_path: Any) -> None:
    cache = _cache(tmp_path)
    _store_known_chain(cache, as_of=_CAPTURE_DATE)
    delisted_conid = _conid_for(_MONTHS["SEP26"], 95.0, "P")

    class _GatewayWithRevalidate(_FakeGateway):
        def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
            params = dict(params or {})
            if path == "/trsrv/secdef":
                self.calls.append((path, params))
                req = [int(c) for c in str(params["conids"]).split(",")]
                return {"secdef": [{"conid": c} for c in req if c != delisted_conid]}
            return super().get(path, params)

    gateway = _GatewayWithRevalidate()
    basket = collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=_SELECTION, discovery_cache=cache, revalidate_cached_conids=True,
    )
    assert basket is not None
    assert all(k.broker_contract_id != str(delisted_conid) for k in basket.instruments)
    assert _discovery_walk_paths(gateway) == []
    assert any(p == "/trsrv/secdef" for p, _ in gateway.calls)


class _CountingGateway:

    def __init__(self, *, warm: set[int], warm_from: int = 1) -> None:
        self.polls = 0
        self._warm = warm
        self._warm_from = warm_from

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert path == "/iserver/marketdata/snapshot"
        self.polls += 1
        conids = [int(c) for c in str((params or {})["conids"]).split(",")]
        rows: list[dict[str, Any]] = []
        for conid in conids:
            if conid in self._warm and self.polls >= self._warm_from:
                rows.append({"conid": conid, "31": "5.0"})
            else:
                rows.append({"conid": conid, "server_id": "q0"})
        return rows


def test_warmup_stops_early_for_a_fully_dead_batch() -> None:
    gateway = _CountingGateway(warm=set())
    rows = snapshot_with_warmup(
        gateway, conids=[1, 2, 3], sleep=lambda _s: None,
        warmup=WarmupConfig(attempts=8, sleep_s=0.0, skip_dead_after=2),
    )
    assert gateway.polls == 3
    assert all(not row.has_market_value() for row in rows)


def test_warmup_skip_dead_after_none_polls_the_full_budget() -> None:
    gateway = _CountingGateway(warm=set())
    snapshot_with_warmup(
        gateway, conids=[1, 2, 3], sleep=lambda _s: None,
        warmup=WarmupConfig(attempts=5, sleep_s=0.0, skip_dead_after=None),
    )
    assert gateway.polls == 5


def test_warmup_still_warms_liquid_contracts_when_a_wing_is_dead() -> None:
    gateway = _CountingGateway(warm={1}, warm_from=2)
    rows = snapshot_with_warmup(
        gateway, conids=[1, 2], sleep=lambda _s: None,
        warmup=WarmupConfig(attempts=8, sleep_s=0.0, skip_dead_after=2),
    )
    by_conid = {row.conid: row.has_market_value() for row in rows}
    assert by_conid[1] is True
    assert by_conid[2] is False


def test_warmup_config_rejects_invalid_budgets() -> None:
    with pytest.raises(ValueError):
        WarmupConfig(attempts=0)
    with pytest.raises(ValueError):
        WarmupConfig(sleep_s=-1.0)
    with pytest.raises(ValueError):
        WarmupConfig(skip_dead_after=0)


def test_cache_writes_only_under_the_given_root(tmp_path: Any) -> None:
    cache = _cache(tmp_path)
    _store_known_chain(cache, as_of=_CAPTURE_DATE)
    written = list(tmp_path.rglob("discovery_conid_cache/**/*.parquet"))
    assert written, "the cache row should have been persisted under the tmp root"
    assert all(str(tmp_path) in str(p) for p in written)


def test_max_age_days_must_be_non_negative(tmp_path: Any) -> None:
    with pytest.raises(ValueError):
        DiscoveryCache(ParquetStore(tmp_path), max_age_days=-1)
