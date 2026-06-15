"""Persisted discovery → conid cache for the CP REST close capture (speed lever B/C).

The dominant wall-clock cost of an option-chain capture is **discovery**: the
``/secdef/strikes`` + ``/secdef/info`` walk fires one rate-limited request per
``(month, strike, right)`` — hundreds per underlying — and the close capture re-runs it
from scratch every fire. Yet the thing it resolves — the
``(underlying, month, strike, right) → conid`` map (plus the discovered strikes and
expiries) — is **static**: it does not change intraday or day-to-day. Only the close
*quotes* need to be fresh. So discovery can be warmed once (even mid-afternoon) and the
close-day capture collapses to the cheap, batched snapshot, with full maturity depth
preserved.

This module is that cache. It is a thin, typed wrapper over the platform's existing
storage spine (:class:`~algotrading.infra.storage.adapter.ParquetStore` + the table
registry): a discovery result lands as one validated row per ``(underlying, as_of_date)``
in a registered ``discovery_conid_cache`` table, so it reads/writes through exactly the
same code path (and immutability rules) as every other table — no bespoke file format.

Three operations, all transport-free and pure over the store:

* :meth:`DiscoveryCache.store` — persist a freshly-discovered chain map under an as-of date.
* :meth:`DiscoveryCache.load` — reload the freshest non-stale entry for an underlying, or
  ``None`` on a miss / staleness (the close capture then falls back to a live walk).
* :meth:`DiscoveryCache.fresh_for` — the staleness predicate, exposed for callers/tests.

Staleness is an explicit, configurable policy: each row carries the ``as_of_date`` the
discovery ran on; a row is *fresh* for a capture date when it is no older than
``max_age_days`` (default :data:`DEFAULT_MAX_AGE_DAYS`). A conid resolved within that window
is treated as valid; an older row is ignored (re-discover). Lever C's bulk
``/trsrv/secdef`` revalidation (:func:`revalidate_conids`) is the optional, 200-per-call
"are these conids still listed?" check the capture can run on the cached conids before
snapshotting, instead of the 1-per-call ``/secdef/info`` walk.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

import structlog
from algotrading.infra.contracts import ConidEntry, DiscoveryCacheRow
from algotrading.infra.storage.adapter import ParquetStore

from ..connectivity.cp_rest_transport import SupportsRestGet

__all__ = [
    "DEFAULT_MAX_AGE_DAYS",
    "TRSRV_SECDEF_BATCH",
    "CachedChain",
    "ConidEntry",
    "DiscoveryCache",
    "DiscoveryCacheRow",
    "revalidate_conids",
]

_LOGGER = structlog.get_logger("ibkr.discovery_cache")

# Default staleness window. The (underlying, month, strike, right) → conid mapping is static
# (a listed contract keeps its conid for its whole life), so a multi-day window is safe; a
# small default keeps a warmed cache from silently outliving a chain re-listing / roll.
DEFAULT_MAX_AGE_DAYS = 5

# IBKR's /trsrv/secdef accepts a comma-separated conid list, documented at 200 conids/request.
TRSRV_SECDEF_BATCH = 200

# The registered table name. Registering into the shared REGISTRY at import keeps the cache a
# first-class, validated, queryable table (same write/read path and immutability rules as every
# other table) without forking a bespoke on-disk format.
# The registered table name — its TableSpec and the DiscoveryCacheRow / ConidEntry contracts now
# live in the central contracts plane (``infra.contracts``), registered alongside every other
# table, so REGISTRY membership is deterministic and not an import-order side-effect of this leaf.
_TABLE = "discovery_conid_cache"


@dataclass(frozen=True, slots=True)
class CachedChain:
    """A warm-hit discovery result: the chain menu and the conid map, reconstructed from cache.

    Returned by :meth:`DiscoveryCache.load`. ``conid_by_contract`` is keyed by the same
    ``f"{expiry.isoformat()}|{strike:.10g}|{right}"`` token the live discovery builds, so the
    capture can swap a warm load in for the live ``_discover_chain`` call byte-for-byte and go
    straight to snapshot. ``expirations`` are ``YYYYMMDD`` tokens and ``strikes`` floats — the
    ``AvailableChain`` ingredients. ``months`` is the listed month set the discovery covered, so a
    fall-back live re-walk targets the same months.
    """

    underlying: str
    as_of_date: date
    exchange: str
    multiplier: str
    months: tuple[str, ...]
    expirations: tuple[str, ...]
    strikes: tuple[float, ...]
    conid_by_contract: Mapping[str, str] = field(default_factory=dict)

    @property
    def conids(self) -> tuple[int, ...]:
        """The distinct integer conids in the map, sorted — the revalidation/snapshot input."""
        seen: set[int] = set()
        for raw in self.conid_by_contract.values():
            try:
                seen.add(int(raw))
            except (TypeError, ValueError):
                continue
        return tuple(sorted(seen))


def _contract_token(expiry: str, strike: float, right: str) -> str:
    """The conid-map key for one (expiry, strike, right) — mirrors the live discovery token.

    ``expiry`` is an ISO date string; the live capture keys on ``date.isoformat()`` and
    ``f"{strike:.10g}"``, so this reproduces that exact token from the stored entry fields.
    """
    return f"{expiry}|{strike:.10g}|{right}"


class DiscoveryCache:
    """Persist and reload the static ``(underlying, month, strike, right) → conid`` map.

    Wraps a :class:`ParquetStore` (the platform's storage spine); a test passes a store rooted at
    a tmp path so nothing ever touches the canonical ``data/`` tree. ``max_age_days`` is the
    staleness window (see the module docstring): :meth:`load` returns the freshest row no older
    than that, else ``None``.
    """

    def __init__(self, store: ParquetStore, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> None:
        if max_age_days < 0:
            raise ValueError(f"max_age_days must be >= 0, got {max_age_days}")
        self._store = store
        self._max_age_days = max_age_days

    @property
    def max_age_days(self) -> int:
        return self._max_age_days

    def store_chain(
        self,
        *,
        underlying: str,
        as_of: date,
        exchange: str,
        multiplier: str,
        months: Sequence[str],
        expirations: Sequence[str],
        strikes: Sequence[float],
        conid_by_contract: Mapping[str, str],
        entry_expiry_by_token: Mapping[str, str] | None = None,
        entry_month_by_token: Mapping[str, str] | None = None,
    ) -> DiscoveryCacheRow:
        """Persist one discovery result for ``underlying`` as known on ``as_of``.

        ``conid_by_contract`` is the live discovery's ``token -> conid`` map (token =
        ``"{expiry}|{strike}|{right}"``). The token already carries the expiry, strike and right,
        so the ``ConidEntry`` rows are reconstructed from it; ``entry_month_by_token`` supplies the
        original listing month per token when the caller has it (else the month is left blank — the
        warm-hit chain reconstruction does not need it, only a same-month live re-walk would). The
        row is written through the store, so it is validated and the append-only immutability rule
        applies (a same-day byte-identical re-store is an idempotent no-op).
        """
        entry_expiry = dict(entry_expiry_by_token or {})
        entry_month = dict(entry_month_by_token or {})
        entries: list[ConidEntry] = []
        for token, conid in conid_by_contract.items():
            expiry, strike, right = _parse_token(token, entry_expiry.get(token))
            entries.append(
                ConidEntry(
                    month=entry_month.get(token, ""),
                    expiry=expiry,
                    strike=strike,
                    right=right,
                    conid=str(conid),
                )
            )
        entries.sort(key=lambda e: (e.expiry, e.strike, e.right))
        row = DiscoveryCacheRow(
            underlying=underlying,
            as_of_date=as_of,
            exchange=exchange,
            multiplier=multiplier,
            months=tuple(months),
            expirations=tuple(expirations),
            strikes=tuple(strikes),
            entries=tuple(entries),
        )
        self._store.write(_TABLE, [row])
        _LOGGER.info(
            "ibkr.discovery_cache.stored",
            underlying=underlying,
            as_of=as_of.isoformat(),
            entries=len(entries),
            expirations=len(row.expirations),
        )
        return row

    def fresh_for(self, *, as_of_date: date, capture_date: date) -> bool:
        """Whether a row discovered on ``as_of_date`` is fresh for a capture on ``capture_date``.

        Fresh when the row is no older than ``max_age_days`` and not from the future. A future
        as-of date (a clock skew / bad write) is treated as not-fresh rather than trusted.
        """
        age = (capture_date - as_of_date).days
        return 0 <= age <= self._max_age_days

    def load(self, *, underlying: str, capture_date: date) -> CachedChain | None:
        """Reload the freshest non-stale discovery for ``underlying`` as of ``capture_date``.

        Reads every persisted row for the underlying, keeps the ones fresh for ``capture_date``
        (:meth:`fresh_for`), and returns the one with the latest ``as_of_date`` as a
        :class:`CachedChain` (chain menu + token→conid map ready to drop into the snapshot path).
        ``None`` on a miss (no rows) or staleness (all rows older than the window) — the signal for
        the capture to fall back to a live ``/secdef`` walk.
        """
        rows = [
            row
            for row in self._store.read(_TABLE, underlying=underlying)
            if isinstance(row, DiscoveryCacheRow)
            and self.fresh_for(as_of_date=row.as_of_date, capture_date=capture_date)
        ]
        if not rows:
            return None
        row = max(rows, key=lambda r: r.as_of_date)
        conid_by_contract = {
            _contract_token(entry.expiry, entry.strike, entry.right): entry.conid
            for entry in row.entries
        }
        _LOGGER.info(
            "ibkr.discovery_cache.hit",
            underlying=underlying,
            as_of=row.as_of_date.isoformat(),
            capture_date=capture_date.isoformat(),
            entries=len(conid_by_contract),
        )
        return CachedChain(
            underlying=row.underlying,
            as_of_date=row.as_of_date,
            exchange=row.exchange,
            multiplier=row.multiplier,
            months=row.months,
            expirations=row.expirations,
            strikes=row.strikes,
            conid_by_contract=conid_by_contract,
        )


def _parse_token(token: str, expiry_hint: str | None) -> tuple[str, float, str]:
    """Split a ``"{expiry}|{strike}|{right}"`` discovery token into its parts.

    ``expiry_hint`` overrides the token's expiry component when the caller carries the canonical
    ISO form separately (it is identical in practice; the hint is belt-and-braces).
    """
    parts = token.split("|")
    if len(parts) != 3:
        raise ValueError(f"malformed discovery token {token!r} (expected 'expiry|strike|right')")
    expiry, strike_text, right = parts
    return (expiry_hint or expiry, float(strike_text), right)


def revalidate_conids(
    transport: SupportsRestGet,
    conids: Sequence[int],
    *,
    batch_size: int = TRSRV_SECDEF_BATCH,
) -> frozenset[int]:
    """Bulk "are these conids still listed?" via ``/trsrv/secdef`` — 200 conids per call (lever C).

    When the capture builds its chain from cached conids, this is the daily validity check, run
    ``batch_size``-per-request (default 200) instead of one ``/secdef/info`` per contract — ~200×
    fewer requests on that path against the 10 req/s ceiling. Returns the subset of ``conids`` the
    gateway still reports as listed (the ``secdef`` response carries one entry per known conid),
    so a caller can drop a delisted conid before snapshotting. A conid absent from the response is
    treated as no-longer-valid.

    The endpoint is a comma-separated GET; the response shape is either ``{"secdef": [...]}`` or a
    bare list of ``{conid: ...}`` entries (both seen on the wire), so both are parsed. An
    unparseable conid in a row is skipped, never raised — a malformed entry must not invalidate the
    whole batch.
    """
    ordered = sorted({int(c) for c in conids})
    if not ordered:
        return frozenset()
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")
    valid: set[int] = set()
    requested = set(ordered)
    for start in range(0, len(ordered), batch_size):
        batch = ordered[start : start + batch_size]
        payload = transport.get(
            "/trsrv/secdef",
            params={"conids": ",".join(str(c) for c in batch)},
        )
        for conid in _parse_secdef_conids(payload):
            if conid in requested:
                valid.add(conid)
    return frozenset(valid)


def _parse_secdef_conids(payload: object) -> set[int]:
    """Pull the integer conids out of a ``/trsrv/secdef`` response (list or secdef-wrapped)."""
    rows = payload.get("secdef") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return set()
    conids: set[int] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        raw = row.get("conid")
        if raw is None:
            continue
        try:
            conids.add(int(raw))
        except (TypeError, ValueError):
            continue
    return conids
