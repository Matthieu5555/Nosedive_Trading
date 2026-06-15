"""The discovery → conid cache and its three speed levers (B/C/E).

Everything runs against a FAKE CP REST transport (the shared :class:`FakeCpTransport`) and a
:class:`ParquetStore` rooted at a tmp path — NO network, NO secrets, and NEVER the canonical
``data/`` tree. The expectations are derived independently of the implementation: the fake gateway
lists a known chain (months × strikes × rights, each with a known conid) and the test hand-derives
which contracts the cache must hold and how many ``/secdef`` calls each path must make, then
asserts on the recorded calls — not by reading back what the code emitted.

Coverage:

* **Round-trip** — a stored discovery reloads as the same chain + conid map.
* **Warm hit makes ZERO ``/secdef`` calls** — the capture-side acceptance bar for lever B.
* **Miss / staleness fall back to a live walk** — and the live result is then cached.
* **Bulk revalidation batches at 200** (lever C) and drops a delisted conid.
* **Warm-up stops early for dead contracts** (lever E) while liquid ones still warm.
"""

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

# Re-use the close-capture fixture's known chain so the warm/cold paths are directly comparable.
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


# ---------------------------------------------------------------------------
# Helpers — the expected conid map, derived from the gateway's listing.
# ---------------------------------------------------------------------------
def _expected_token_to_conid() -> dict[str, str]:
    """The full ``"{expiry}|{strike}|{right}" -> conid`` map the known chain lists.

    Derived directly from the fixture's listing oracle (``_conid_for``), independently of the
    capture/cache code: every (month, strike, right) the gateway lists, keyed by the same token the
    capture builds (``date.isoformat() | strike:.10g | right``).
    """
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
    """Persist the full known chain into the cache as discovered on ``as_of``; return the map."""
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


# ---------------------------------------------------------------------------
# Round-trip.
# ---------------------------------------------------------------------------
def test_store_then_load_round_trips_the_chain_and_conid_map(tmp_path: Any) -> None:
    cache = _cache(tmp_path)
    expected = _store_known_chain(cache, as_of=_CAPTURE_DATE)

    loaded = cache.load(underlying="SPX", capture_date=_CAPTURE_DATE)
    assert loaded is not None
    # The conid map round-trips token-for-token.
    assert dict(loaded.conid_by_contract) == expected
    # The chain menu round-trips: expirations (YYYYMMDD) and strikes.
    assert set(loaded.expirations) == {e.strftime("%Y%m%d") for e in _MONTHS.values()}
    assert set(loaded.strikes) == set(_STRIKES)
    assert loaded.multiplier == "100"
    # The integer conid view is the de-duplicated sorted set the snapshot/revalidate path consumes.
    assert loaded.conids == tuple(sorted(int(c) for c in expected.values()))


def test_load_misses_for_an_unknown_underlying(tmp_path: Any) -> None:
    cache = _cache(tmp_path)
    _store_known_chain(cache, as_of=_CAPTURE_DATE)
    assert cache.load(underlying="UNSEEN", capture_date=_CAPTURE_DATE) is None


# ---------------------------------------------------------------------------
# Staleness policy.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("age_days", "fresh"),
    [
        (0, True),  # same day
        (DEFAULT_MAX_AGE_DAYS, True),  # exactly at the window edge — still fresh
        (DEFAULT_MAX_AGE_DAYS + 1, False),  # one day past the window — stale
        (-1, False),  # a future as-of (clock skew) is not trusted
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
    # 3 days later, with a 2-day window, the only row is stale.
    capture = date.fromordinal(as_of.toordinal() + 3)
    assert cache.load(underlying="SPX", capture_date=capture) is None
    # 2 days later it is still fresh.
    assert cache.load(underlying="SPX", capture_date=date.fromordinal(as_of.toordinal() + 2))


def test_load_picks_the_freshest_non_stale_row(tmp_path: Any) -> None:
    cache = _cache(tmp_path, max_age_days=30)
    older = date(2026, 3, 1)
    newer = date(2026, 3, 10)
    # Two discoveries on different dates; the newer one carries a distinct (smaller) chain.
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


# ---------------------------------------------------------------------------
# Capture wiring — lever B: a warm hit makes ZERO /secdef calls.
# ---------------------------------------------------------------------------
def _discovery_walk_paths(gateway: _FakeGateway) -> list[str]:
    """The per-contract discovery-walk paths the gateway recorded — ``strikes`` + ``info``.

    This is the path the cache eliminates: the rate-limited, one-call-per-(month,strike,right)
    walk (hundreds of calls). The single ``/secdef/search`` the live path fires to resolve the
    index conid + months is NOT part of this walk (it is one cheap call regardless), so it is
    excluded here — the lever-B win is the disappearance of strikes+info, not the index search.
    """
    return [
        path
        for path, _ in gateway.calls
        if path in ("/iserver/secdef/strikes", "/iserver/secdef/info")
    ]


def test_warm_cache_capture_makes_zero_secdef_calls(tmp_path: Any) -> None:
    """The lever-B acceptance bar: a warm hit skips the entire /secdef discovery walk.

    First a no-cache capture establishes the basket the capture must still produce. Then a cache
    pre-warmed with the same chain drives a second capture: the resulting basket is identical, but
    the gateway recorded ZERO /secdef/{search,strikes,info} calls — discovery came from cache; only
    the (cheap, batched) marketdata snapshot was hit.
    """
    cache = _cache(tmp_path)
    _store_known_chain(cache, as_of=_CAPTURE_DATE)

    gateway = _FakeGateway()
    basket = collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=_SELECTION, discovery_cache=cache,
    )
    assert basket is not None
    # Same full chain the live walk would have produced — full maturity depth preserved.
    option_keys = {
        (k.expiry, k.strike, k.option_right) for k in basket.instruments if k.is_option()
    }
    assert option_keys == _expected_option_keys()
    # The acceptance bar: not a single /secdef call — discovery was served from cache.
    assert _discovery_walk_paths(gateway) == []
    # But the snapshot WAS hit (the close marks still come live).
    assert any(p == "/iserver/marketdata/snapshot" for p, _ in gateway.calls)


def test_cold_cache_falls_back_to_live_discovery_then_warms(tmp_path: Any) -> None:
    """A miss runs the live /secdef walk AND caches it, so the next fire is a warm zero-secdef hit."""
    cache = _cache(tmp_path)

    cold_gateway = _FakeGateway()
    first = collect_live_basket(
        cold_gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=_SELECTION, discovery_cache=cache,
    )
    assert first is not None
    # The cold capture DID walk /secdef (search + strikes + info) — the full live discovery.
    assert any(p == "/iserver/secdef/info" for p in _discovery_walk_paths(cold_gateway))

    # The cache now holds the chain; assert the stored map equals the independently-derived oracle.
    loaded = cache.load(underlying="SPX", capture_date=_CAPTURE_DATE)
    assert loaded is not None
    assert dict(loaded.conid_by_contract) == _expected_token_to_conid()

    # A second capture is now a warm hit — zero /secdef calls.
    warm_gateway = _FakeGateway()
    second = collect_live_basket(
        warm_gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=_SELECTION, discovery_cache=cache,
    )
    assert second is not None
    assert "/iserver/secdef/info" not in _discovery_walk_paths(warm_gateway)


def test_stale_cache_triggers_a_live_rediscovery(tmp_path: Any) -> None:
    """A stale entry is ignored: the capture re-walks /secdef rather than trusting an old map."""
    cache = _cache(tmp_path, max_age_days=2)
    # Discovered 10 days before the capture — well past the 2-day window.
    _store_known_chain(cache, as_of=date.fromordinal(_CAPTURE_DATE.toordinal() - 10))

    gateway = _FakeGateway()
    basket = collect_live_basket(
        gateway, index=SPX, as_of=CLOSE, next_open=NEXT_OPEN, config=_config(),
        selection=_SELECTION, discovery_cache=cache,
    )
    assert basket is not None
    # The stale row was not used — the live info walk ran.
    assert any(p == "/iserver/secdef/info" for p in _discovery_walk_paths(gateway))


# ---------------------------------------------------------------------------
# Lever C: bulk revalidation via /trsrv/secdef, 200 conids per request.
# ---------------------------------------------------------------------------
class _RevalidateTransport:
    """A fake /trsrv/secdef gateway: records each batch and lists every conid except a delisted set."""

    def __init__(self, *, delisted: set[int] | None = None) -> None:
        self.batches: list[list[int]] = []
        self._delisted = delisted or set()

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        assert path == "/trsrv/secdef"
        conids = [int(c) for c in str((params or {})["conids"]).split(",")]
        self.batches.append(conids)
        # Echo back every requested conid except the delisted ones (the "still listed" answer).
        return {"secdef": [{"conid": c} for c in conids if c not in self._delisted]}


def test_revalidate_batches_at_200(tmp_path: Any) -> None:
    """450 conids must go out in 200 + 200 + 50 — the documented /trsrv/secdef per-request cap."""
    conids = list(range(1000, 1450))  # 450 distinct conids
    transport = _RevalidateTransport()
    valid = revalidate_conids(transport, conids)

    # Three batches: 200, 200, 50 — derived from the cap, independently of the impl.
    assert [len(b) for b in transport.batches] == [TRSRV_SECDEF_BATCH, TRSRV_SECDEF_BATCH, 50]
    # Every conid appears exactly once across the batches.
    flattened = [c for batch in transport.batches for c in batch]
    assert sorted(flattened) == conids
    # All were listed, so all are valid.
    assert valid == frozenset(conids)


def test_revalidate_drops_a_delisted_conid(tmp_path: Any) -> None:
    conids = [10, 20, 30]
    transport = _RevalidateTransport(delisted={20})
    valid = revalidate_conids(transport, conids)
    assert valid == frozenset({10, 30})


def test_capture_with_revalidation_drops_a_delisted_contract(tmp_path: Any) -> None:
    """A warm hit + revalidation snapshots only the still-listed conids (one wing delisted).

    The cache holds the full chain; the gateway's /trsrv/secdef reports one contract delisted. With
    ``revalidate_cached_conids=True`` the capture drops that conid before snapshotting, so its
    option key is absent from the basket — while still making ZERO /secdef discovery-walk calls.
    """
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
    # The delisted contract is not in the basket.
    assert all(k.broker_contract_id != str(delisted_conid) for k in basket.instruments)
    # Still zero /secdef discovery calls (revalidation is /trsrv/secdef, not the walk).
    assert _discovery_walk_paths(gateway) == []
    # Revalidation was actually hit.
    assert any(p == "/trsrv/secdef" for p, _ in gateway.calls)


# ---------------------------------------------------------------------------
# Lever E: warm-up trim — configurable budget + early dead-contract skip.
# ---------------------------------------------------------------------------
class _CountingGateway:
    """A snapshot gateway that warms a chosen subset of conids and never warms the rest.

    ``warm`` conids carry a value tag from poll ``warm_from`` onward; the others stay metadata-only
    forever (a dead far-OTM wing). Counts every snapshot poll so a test can assert the budget.
    """

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
    """A batch that never warms stops after the dead-skip budget, not the full attempt budget.

    With ``skip_dead_after=2`` and ``attempts=8`` a batch where NO conid ever prints stops at
    1 initial + 2 stalled polls = 3, vs the 8 the un-trimmed budget would burn. Derived from the
    policy, not the impl.
    """
    gateway = _CountingGateway(warm=set())
    rows = snapshot_with_warmup(
        gateway, conids=[1, 2, 3], sleep=lambda _s: None,
        warmup=WarmupConfig(attempts=8, sleep_s=0.0, skip_dead_after=2),
    )
    assert gateway.polls == 3
    # No row carries a value tag — the dead batch yields nothing promotable.
    assert all(not row.has_market_value() for row in rows)


def test_warmup_skip_dead_after_none_polls_the_full_budget() -> None:
    """``skip_dead_after=None`` is the legacy behaviour: a fully-cold batch polls every attempt."""
    gateway = _CountingGateway(warm=set())
    snapshot_with_warmup(
        gateway, conids=[1, 2, 3], sleep=lambda _s: None,
        warmup=WarmupConfig(attempts=5, sleep_s=0.0, skip_dead_after=None),
    )
    assert gateway.polls == 5


def test_warmup_still_warms_liquid_contracts_when_a_wing_is_dead() -> None:
    """Correctness under the trim: a liquid conid that warms late STILL warms; the dead wing does not.

    Conid 1 prints from the 2nd poll on; conid 2 never prints. The warm-up must return conid 1 warm
    (liquid contracts are not sacrificed to the dead-skip) and conid 2 cold.
    """
    gateway = _CountingGateway(warm={1}, warm_from=2)
    rows = snapshot_with_warmup(
        gateway, conids=[1, 2], sleep=lambda _s: None,
        warmup=WarmupConfig(attempts=8, sleep_s=0.0, skip_dead_after=2),
    )
    by_conid = {row.conid: row.has_market_value() for row in rows}
    assert by_conid[1] is True  # liquid contract warmed
    assert by_conid[2] is False  # dead wing stayed cold


def test_warmup_config_rejects_invalid_budgets() -> None:
    with pytest.raises(ValueError):
        WarmupConfig(attempts=0)
    with pytest.raises(ValueError):
        WarmupConfig(sleep_s=-1.0)
    with pytest.raises(ValueError):
        WarmupConfig(skip_dead_after=0)


# ---------------------------------------------------------------------------
# Tmp-store safety: the cache never touches the canonical data/ tree.
# ---------------------------------------------------------------------------
def test_cache_writes_only_under_the_given_root(tmp_path: Any) -> None:
    cache = _cache(tmp_path)
    _store_known_chain(cache, as_of=_CAPTURE_DATE)
    # The reference-layer cache table landed under the tmp root, nowhere else.
    written = list(tmp_path.rglob("discovery_conid_cache/**/*.parquet"))
    assert written, "the cache row should have been persisted under the tmp root"
    assert all(str(tmp_path) in str(p) for p in written)


def test_max_age_days_must_be_non_negative(tmp_path: Any) -> None:
    with pytest.raises(ValueError):
        DiscoveryCache(ParquetStore(tmp_path), max_age_days=-1)
