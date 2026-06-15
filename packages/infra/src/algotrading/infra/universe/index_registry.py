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

Validation rides the same pydantic v2 seam as the economic config sections
(``core.config``'s ``_SECTION_CONFIG`` discipline: frozen + ``strict`` + ``extra="forbid"``,
REP6/M16): strict mode rejects a YAML ``true`` for a conid natively, ``extra="forbid"``
replaces the hand allow-lists, and the one load-bearing bespoke rule — reject an unknown
calendar code, **never** silently default it (a typo like ``XEURX`` falling back to some
calendar would capture the wrong close instant, a look-ahead bug) — is a ``field_validator``
against the live ``exchange_calendars`` name set. A pydantic ``ValidationError`` is mapped
onto the labeled :class:`IndexRegistryError` (symbol, field, value, reason) exactly as
core's config boundary maps onto ``ConfigFieldError``, so a bad entry names what was wrong
the same way it always did.

The ``ibkr:`` sub-block is the only provider-specific part; symbol/name/calendar/currency
describe the index and stay provider-neutral, so a future Saxo/Deribit sibling sub-block
joins under the same key without disturbing the core (ADR 0023's multi-provider stance).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from typing import Annotated, NoReturn

import exchange_calendars as xcals
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from .errors import IndexRegistryError


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must be a non-empty string")
    return value


def _require_iso_currency(value: str) -> str:
    if not (len(value) == 3 and value.isalpha() and value.isupper()):
        raise ValueError("must be a 3-letter uppercase ISO code")
    return value


# A string that must carry content — strict mode already rejects non-strings, this
# rejects the present-but-blank operator error ("symbol: ' '").
_NonBlankStr = Annotated[str, AfterValidator(_require_non_blank)]


class _IbkrRefModel(BaseModel):
    """The validation schema for the ``ibkr:`` sub-block (strict, no unknown keys).

    ``secType`` is the on-disk spelling (an alias for ``sec_type``); ``conid`` ``0`` is the
    unverified placeholder, so ``>= 0``; a *pinned* constituent conid exists to name a real
    contract, so ``> 0``. Strict mode rejects a YAML ``true`` for any conid natively.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    conid: int = Field(ge=0)
    sec_type: str = Field(alias="secType")
    exchange: _NonBlankStr
    symbol: _NonBlankStr | None = None
    # An absent or explicitly-null pin map means "no pins" (the historical contract);
    # anything else must be a mapping of non-blank label -> positive verified conid.
    constituent_conids: Annotated[
        dict[_NonBlankStr, Annotated[int, Field(gt=0)]],
        BeforeValidator(lambda value: {} if value is None else value),
    ] = Field(default_factory=dict)

    @field_validator("sec_type")
    @classmethod
    def _sec_type_non_blank(cls, value: str) -> str:
        return _require_non_blank(value)


class _IndexEntryModel(BaseModel):
    """The validation schema for one registry entry (strict, no unknown keys).

    Every field is required — a missing one is a labeled error, never defaulted. The
    calendar check needs the live ``exchange_calendars`` name set, passed via the
    validation context so the model stays a pure schema.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: _NonBlankStr
    calendar: str
    # Optional local-time override for the close instant — see the matching field on
    # :class:`IndexEntry`. Absent/null = derive the close from the calendar verbatim.
    option_settlement_close: str | None = None
    currency: Annotated[str, AfterValidator(_require_iso_currency)]
    ibkr: _IbkrRefModel
    enabled: bool

    @field_validator("calendar")
    @classmethod
    def _known_calendar(cls, value: str, info: ValidationInfo) -> str:
        # The load-bearing negative: an unknown calendar code is rejected here, NEVER
        # coerced to some default calendar. A silent fallback would resolve the wrong
        # session close.
        _require_non_blank(value)
        known: frozenset[str] = (info.context or {}).get("known_calendars", frozenset())
        if value not in known:
            raise ValueError(
                "unknown exchange_calendars code (not in get_calendar_names()); "
                "an unknown calendar is never defaulted"
            )
        return value

    @field_validator("option_settlement_close")
    @classmethod
    def _valid_settlement_close(cls, value: str | None) -> str | None:
        # An ISO 24-hour time-of-day ("HH:MM"); a malformed value is rejected here, never
        # silently dropped — a defaulted settlement close would capture the wrong instant
        # (the same look-ahead hazard the calendar check guards). Parsed to a `time` in
        # `_parse_entry`; validated here so a bad entry names the field the registry way.
        if value is None:
            return None
        try:
            time.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("must be an ISO 24-hour time-of-day 'HH:MM' (e.g. '17:30')") from exc
        return value


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
    # The symbol to search IBKR's ``secdef`` by, when it differs from the registry key. IBKR does
    # not list every index under its common code (e.g. Euro Stoxx 50 is ``ESTX50`` on IBKR, not the
    # registry's ``SX5E``); set ``ibkr.symbol`` to the IBKR ticker and resolution uses it while the
    # rest of the platform keeps the registry symbol. ``None`` (the default) means same as the key.
    symbol: str | None = None
    # Verified conid pins for individual constituents whose bare ticker the ``/secdef/search`` door
    # cannot resolve unambiguously — a ticker shared by two listings (Euronext-Paris ``SAN`` is
    # Sanofi, Bolsa-de-Madrid ``SAN`` is Banco Santander; IBKR even renames one to ``SAN1``) or a
    # name search returns junk for. Each ``label -> conid`` pin is fetched by its unique conid (the
    # actual identifier), bypassing the search; ``label`` is the underlying key the bars store
    # under. A frozen tuple of pairs (not a dict) so the dataclass stays hashable. Default: no pins.
    constituent_conids: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One index the platform tracks: its identity, calendar, currency, and IBKR ref.

    ``symbol`` is the registry key and the one vocabulary shared across membership (1A),
    capture (1C), the cron (1G), and the front (1I). ``calendar`` is an ``exchange_calendars``
    code (validated against the library's known names at parse time, never defaulted) from
    which the close instant is *derived* at run time — the resolved time is not stored.
    ``enabled`` is the on/off switch the scheduler reads.

    ``option_settlement_close`` is an optional time-of-day override (a stdlib
    :class:`datetime.time`) for the close instant: the calendar still owns the trading-*day*
    set and DST, but where the index's options settle at a different time than the calendar's
    own close, this pins that time-of-day. SX5E is the live case — its OESX options settle
    17:30 CET, while the XEUR calendar's close is the 22:00 CET *futures* close. ``None`` (the
    default) means derive the close from the calendar verbatim (correct for SPX/XNYS, where
    the index and its options share the 16:00 ET close).
    """

    symbol: str
    name: str
    calendar: str
    currency: str
    ibkr: IbkrRef
    enabled: bool
    option_settlement_close: time | None = None

    @property
    def ibkr_search_symbol(self) -> str:
        """The symbol to resolve this index against IBKR — ``ibkr.symbol`` override, else the key.

        Used only at the IBKR ``secdef`` resolution door (conid + option discovery); every other
        seam (membership, keys, the front) keeps :attr:`symbol`, the platform-wide vocabulary.
        """
        return self.ibkr.symbol or self.symbol


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


def _raise_registry_error(symbol: str, exc: ValidationError) -> NoReturn:
    """Map a pydantic ``ValidationError`` onto the labeled :class:`IndexRegistryError`.

    Takes the first reported error, joins its location into the dotted ``field`` the
    registry has always named (``ibkr.conid``, ``ibkr.constituent_conids.SAN1``), and
    carries the offending input value plus a plain-language reason — the same boundary
    discipline as core's ``ConfigFieldError`` mapper, so callers and tests keep the
    symbol/field/value/reason semantics rather than a pydantic traceback.
    """
    error = exc.errors()[0]
    location = error.get("loc", ())
    # "[key]" is pydantic's marker for a bad mapping *key* (a blank pin label); the
    # offending mapping is already named by the rest of the path.
    parts = [str(part) for part in location if part != "[key]"]
    field = ".".join(parts) if parts else "<entry>"
    kind = error.get("type", "")
    if kind == "missing":
        raise IndexRegistryError(symbol, field, None, "missing field") from exc
    if kind == "extra_forbidden":
        raise IndexRegistryError(symbol, field, error.get("input"), "unknown key") from exc
    raise IndexRegistryError(symbol, field, error.get("input"), error.get("msg", "")) from exc


def _thaw(value: object) -> object:
    """Deep-copy nested mappings into plain dicts (strict pydantic accepts only ``dict``).

    The loaded config deep-freezes the ``indices:`` block into ``MappingProxyType`` for
    stable hashing; strict-mode validation requires real ``dict`` instances. Values are
    untouched — strictness still rejects every wrong leaf type.
    """
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    return value


def _parse_entry(symbol: str, raw: object, known_calendars: frozenset[str]) -> IndexEntry:
    if not symbol or not symbol.strip():
        raise IndexRegistryError(symbol, "symbol", symbol, "must be a non-empty string")
    try:
        model = _IndexEntryModel.model_validate(
            _thaw(raw), context={"known_calendars": known_calendars}
        )
    except ValidationError as exc:
        _raise_registry_error(symbol, exc)
    return IndexEntry(
        symbol=symbol,
        name=model.name,
        calendar=model.calendar,
        currency=model.currency,
        option_settlement_close=(
            time.fromisoformat(model.option_settlement_close)
            if model.option_settlement_close is not None
            else None
        ),
        ibkr=IbkrRef(
            conid=model.ibkr.conid,
            sec_type=model.ibkr.sec_type,
            exchange=model.ibkr.exchange,
            symbol=model.ibkr.symbol,
            constituent_conids=tuple(model.ibkr.constituent_conids.items()),
        ),
        enabled=model.enabled,
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
