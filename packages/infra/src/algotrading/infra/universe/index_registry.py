"""The index registry: which indices the platform tracks, typed and validated.

This is the typed view over the ``indices:`` block in ``configs/universe.yaml`` (ADR
0035). Each entry names one index the platform operates on — its symbol, display name,
trading-calendar code, currency, IBKR contract reference, and an ``enabled`` switch.

The block lives inside the already-hashed ``universe`` bundle (``config_hashes["universe"]``,
ADR 0028): it changes *which records exist* (enabling SX5E means SX5E snapshots/bars start
landing), so it is economic config, not operational. It introduces **no separate hash** —
it travels with the rest of ``universe.yaml``.

What the registry is *not*: it is *which indices*, never *what is inside them*. Membership
(1A ``IndexConstituent``) is a separate, bitemporal, look-ahead-gated concern and must not
be folded in here (ADR 0035 §3).

Why a bespoke parser rather than the reflective ``build_dataclass`` seam: that seam coerces
flat scalar/tuple fields, but the registry is a *keyed map of nested dataclasses* with a
calendar-code that must validate against a live library name set. That validation — reject
an unknown calendar code, never silently default it — is the load-bearing rule (a typo like
``XEURX`` falling back to some calendar would capture the wrong close instant, a look-ahead
bug). So the parsing lives here, beside the calendar resolver that consumes the same codes.

The ``ibkr:`` sub-block is the only provider-specific part; symbol/name/calendar/currency
describe the index and stay provider-neutral, so a future Saxo/Deribit sibling sub-block
joins under the same key without disturbing the core (ADR 0023's multi-provider stance).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import exchange_calendars as xcals

from .errors import IndexRegistryError

# The provider-agnostic registry fields plus the one provider sub-block. Any other key in
# an entry is rejected (a typo must fail loudly, not be ignored), the same discipline the
# reflective config loader enforces for the flat economic sections.
_ENTRY_FIELDS = frozenset({"name", "calendar", "currency", "ibkr", "enabled"})
_IBKR_FIELDS = frozenset({"conid", "secType", "exchange"})


@dataclass(frozen=True, slots=True)
class IbkrRef:
    """The IBKR contract resolution for one index — the provider-specific sub-block.

    ``conid`` is IBKR's numeric contract id (``0`` is the unverified placeholder). An entry may
    be ``enabled: true`` while its conid is still ``0``: the conid is consumed only by the 1C
    broker→raw-event qualification seam, so the calendar/close-capture/projection path runs
    without it. A placeholder is left at ``0`` rather than guessed — a wrong conid silently
    qualifies the wrong contract — and is replaced with the verified id before 1C qualifies it.
    ``sec_type``/``exchange`` are the IBKR security type and routing exchange.
    """

    conid: int
    sec_type: str
    exchange: str


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One index the platform tracks: its identity, calendar, currency, and IBKR ref.

    ``symbol`` is the registry key and the one vocabulary shared across membership (1A),
    capture (1C), the cron (1G), and the front (1I). ``calendar`` is an ``exchange_calendars``
    code (validated against the library's known names at parse time, never defaulted) from
    which the close instant is *derived* at run time — the resolved time is not stored.
    ``enabled`` is the on/off switch the scheduler reads.
    """

    symbol: str
    name: str
    calendar: str
    currency: str
    ibkr: IbkrRef
    enabled: bool


@dataclass(frozen=True, slots=True)
class IndexRegistry:
    """The full set of registry entries, keyed by index symbol.

    Built by :func:`parse_index_registry` from the ``indices:`` block. An empty block is
    valid — it yields an empty registry (and an empty enabled set), not a crash.
    """

    entries: tuple[IndexEntry, ...]

    def __post_init__(self) -> None:
        symbols = [entry.symbol for entry in self.entries]
        if len(set(symbols)) != len(symbols):
            dupes = sorted({s for s in symbols if symbols.count(s) > 1})
            raise IndexRegistryError(
                dupes[0], "symbol", dupes[0], "duplicate index symbol in registry"
            )

    def get(self, symbol: str) -> IndexEntry:
        """Return the entry for an index symbol, or raise a labeled error if unknown."""
        for entry in self.entries:
            if entry.symbol == symbol:
                return entry
        known = tuple(sorted(e.symbol for e in self.entries))
        raise IndexRegistryError(symbol, "symbol", symbol, f"not in registry; known: {known!r}")

    def enabled_indices(self) -> tuple[IndexEntry, ...]:
        """The enabled entries only, in sorted-symbol order — the single downstream seam.

        A disabled index is absent here and so never reaches capture (1C), the cron (1G),
        membership resolution (1A), or the front picker (1I). Order is canonical (by symbol)
        so the enabled set is stable across runs.
        """
        return tuple(sorted((e for e in self.entries if e.enabled), key=lambda e: e.symbol))


def _require_str(symbol: str, field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IndexRegistryError(symbol, field, value, "must be a non-empty string")
    return value


def _parse_ibkr(symbol: str, raw: object) -> IbkrRef:
    if not isinstance(raw, Mapping):
        raise IndexRegistryError(symbol, "ibkr", raw, "must be a mapping")
    unknown = set(raw) - _IBKR_FIELDS
    if unknown:
        bad = sorted(unknown)[0]
        raise IndexRegistryError(symbol, f"ibkr.{bad}", raw[bad], "unknown key")
    missing = _IBKR_FIELDS - set(raw)
    if missing:
        raise IndexRegistryError(symbol, f"ibkr.{sorted(missing)[0]}", None, "missing field")
    conid = raw["conid"]
    # bool is an int subclass; a YAML `true` for a conid is a config error, not a 1.
    if isinstance(conid, bool) or not isinstance(conid, int):
        raise IndexRegistryError(symbol, "ibkr.conid", conid, "must be an integer")
    if conid < 0:
        raise IndexRegistryError(symbol, "ibkr.conid", conid, "must be >= 0")
    return IbkrRef(
        conid=conid,
        sec_type=_require_str(symbol, "ibkr.secType", raw["secType"]),
        exchange=_require_str(symbol, "ibkr.exchange", raw["exchange"]),
    )


def _parse_entry(symbol: str, raw: object, known_calendars: frozenset[str]) -> IndexEntry:
    if not symbol or not symbol.strip():
        raise IndexRegistryError(symbol, "symbol", symbol, "must be a non-empty string")
    if not isinstance(raw, Mapping):
        raise IndexRegistryError(symbol, "<entry>", raw, "must be a mapping")
    unknown = set(raw) - _ENTRY_FIELDS
    if unknown:
        bad = sorted(unknown)[0]
        raise IndexRegistryError(symbol, bad, raw[bad], "unknown key")
    missing = _ENTRY_FIELDS - set(raw)
    if missing:
        raise IndexRegistryError(symbol, sorted(missing)[0], None, "missing field")

    calendar = _require_str(symbol, "calendar", raw["calendar"])
    # The load-bearing negative: an unknown calendar code is rejected here, NEVER coerced to
    # some default calendar. A silent fallback would resolve the wrong session close.
    if calendar not in known_calendars:
        raise IndexRegistryError(
            symbol,
            "calendar",
            calendar,
            "unknown exchange_calendars code (not in get_calendar_names()); "
            "an unknown calendar is never defaulted",
        )

    currency = _require_str(symbol, "currency", raw["currency"])
    if not (len(currency) == 3 and currency.isalpha() and currency.isupper()):
        raise IndexRegistryError(
            symbol, "currency", currency, "must be a 3-letter uppercase ISO code"
        )

    enabled = raw["enabled"]
    if not isinstance(enabled, bool):
        raise IndexRegistryError(symbol, "enabled", enabled, "must be a boolean")

    return IndexEntry(
        symbol=symbol,
        name=_require_str(symbol, "name", raw["name"]),
        calendar=calendar,
        currency=currency,
        ibkr=_parse_ibkr(symbol, raw["ibkr"]),
        enabled=enabled,
    )


def parse_index_registry(block: Mapping[str, object] | None) -> IndexRegistry:
    """Parse and validate the ``indices:`` block into a frozen :class:`IndexRegistry`.

    ``block`` is the keyed map (index symbol → entry) read from ``universe.yaml``; ``None``
    or an empty mapping yields an empty registry (a valid, empty enabled set, not a crash).
    Every entry is validated: non-empty symbol; ``calendar`` a code the library actually
    knows (validated against :func:`exchange_calendars.get_calendar_names`, rejected — never
    defaulted — on a typo); ``currency`` a 3-letter uppercase ISO code; ``ibkr.secType``/
    ``exchange`` non-empty and ``conid`` a non-negative int; ``enabled`` a bool. A bad field
    raises :class:`IndexRegistryError` naming the index, field, value, and reason.
    """
    if block is None:
        return IndexRegistry(entries=())
    if not isinstance(block, Mapping):
        raise IndexRegistryError("<indices>", "<root>", block, "must be a mapping")
    known_calendars = frozenset(xcals.get_calendar_names())
    entries = tuple(
        _parse_entry(str(symbol), raw, known_calendars)
        for symbol, raw in sorted(block.items(), key=lambda kv: str(kv[0]))
    )
    return IndexRegistry(entries=entries)
