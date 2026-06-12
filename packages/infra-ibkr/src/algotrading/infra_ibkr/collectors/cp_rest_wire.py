"""Typed CP REST wire models (pydantic v2) — one validated shape per broker payload.

Every Client Portal payload the collectors consume used to be spelunked as untyped ``Any``
with per-call-site ``isinstance``/``.get`` chains, and the "coerce one broker scalar" job
existed several times with drifting semantics. This module is the single home for those wire
shapes: a pydantic model per payload (snapshot row, secdef search row, strikes, secdef info,
history bar) with ``extra="ignore"``, and the bespoke parse/coercion functions moved **verbatim**
into ``Annotated[..., BeforeValidator(...)]`` types so the emitted values stay byte-identical to
the hand-rolled parsers they replace (the ``test_cp_rest_equivalence.py`` bar).

Row-skip semantics are the callers': a model here never widens an error. The scalar validators
(:func:`parse_field_value`, :func:`coerce_int_or_none`, :func:`coerce_text`) degrade to
``None``/text rather than raising, exactly like the functions they were moved from; the strict
:func:`require_bar_number` raises (wrapped into a ``ValidationError``) because the history
normalizer's contract is to *reject* a dishonest bar, never coerce it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

# IBKR's "no value available" sentinel (mirrors the TWS adapter's -1 drop).
_NO_VALUE = -1.0

# The market-data field tags the snapshot/WS normalizer understands — what to request on
# snapshot/subscribe, in the deterministic emit order: bid, ask, sizes, last, last size.
# Codes per the CP Web API (interactivebrokers.github.io/cpwebapi); they MUST map onto the same
# names the Nautilus path uses or the equivalence test fails.
SNAPSHOT_FIELD_TAGS: tuple[str, ...] = ("84", "86", "88", "85", "31", "7059")


def parse_field_value(raw: object) -> float | None:
    """Parse a CP field value to a float, or ``None`` if absent / sentinel / non-finite.

    CP returns field values as strings, occasionally prefixed with a status flag (e.g. ``"C189.5"``
    when the last is the prior close, ``"H..."`` halted). Strip a leading non-numeric flag, then
    parse; drop the ``-1`` sentinel and any non-finite result.

    Moved verbatim from ``cp_rest_normalize._parse_value`` — the value-parse surface feeds
    persisted events, so this function is hash-gated (byte-identical outputs).
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if text and not (text[0].isdigit() or text[0] in "+-."):
        text = text[1:].strip()  # drop a leading status flag like 'C' / 'H'
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    if not math.isfinite(value) or value == _NO_VALUE:
        return None
    return value


def coerce_int_or_none(value: object) -> int | None:
    """Coerce a broker-supplied scalar to ``int``, or ``None`` when it is not coercible.

    The broker's conid / ``_updated`` fields are nominally integers but ride an untyped JSON
    payload, so an unexpected shape (``None``, a non-numeric string, a dict) must degrade to a
    structured skip at the call site rather than raise a bare ``ValueError`` and abort the whole
    capture — mirroring the guarded ``float()`` parsing of the mark fields. ``bool`` is rejected
    because a JSON ``true``/``false`` is never a valid conid or millisecond timestamp.

    Moved verbatim from ``cp_rest_close_capture._as_int_or_none``.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def coerce_text(value: object) -> str:
    """A secdef textual field as text: absent/null → ``""``, anything else → ``str(value)``.

    The model counterpart of the ``str(item.get(key) or "")`` idiom the hand-rolled parsers
    used on ``symbol``/``secType``/``description``/``months`` — selection-only strings, never
    persisted.
    """
    return "" if value is None else str(value)


def require_bar_number(value: object) -> float:
    """A history-bar numeric field, or a ``ValueError`` the model wraps into a labeled rejection.

    Moved verbatim (the per-value core) from ``cp_rest_history_normalize._require_number``: a
    bool or a non-``int``/``float`` (including a numeric *string*) is rejected — the normalize
    door refuses to coerce a dishonest bar — and so is a non-finite value.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"must be numeric, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"is not finite: {value!r}")
    return number


def keep_mappings(value: object) -> tuple[object, ...]:
    """A wire list pre-filtered to its mapping entries (a non-list degrades to empty).

    The per-row skip the hand-rolled parsers applied to ``sections`` lists: a row whose
    ``sections`` is not a list, or an entry that is not an object, is skipped — never an error.
    """
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


# The shared coercion types: each is one bespoke parser moved into an Annotated validator.
MarketFieldValue = Annotated[float | None, BeforeValidator(parse_field_value)]
LooseInt = Annotated[int | None, BeforeValidator(coerce_int_or_none)]
BarNumber = Annotated[float, BeforeValidator(require_bar_number)]
WireText = Annotated[str, BeforeValidator(coerce_text)]

_WIRE_MODEL_CONFIG = ConfigDict(extra="ignore", frozen=True)


class SnapshotRow(BaseModel):
    """One ``/iserver/marketdata/snapshot`` row or WS ``smd+`` frame, extra keys ignored.

    The six value tags parse through :data:`MarketFieldValue` (status-flag strip, ``-1``
    sentinel drop) — an absent tag and an unparseable tag are both ``None``, exactly the
    skip semantics ``snapshot_to_events`` always had. ``conid`` / ``_updated`` coerce through
    :data:`LooseInt` so a malformed scalar skips the row instead of raising.
    """

    model_config = _WIRE_MODEL_CONFIG

    conid: LooseInt = None
    updated_ms: LooseInt = Field(default=None, alias="_updated")
    bid: MarketFieldValue = Field(default=None, alias="84")
    ask: MarketFieldValue = Field(default=None, alias="86")
    bid_size: MarketFieldValue = Field(default=None, alias="88")
    ask_size: MarketFieldValue = Field(default=None, alias="85")
    last: MarketFieldValue = Field(default=None, alias="31")
    last_size: MarketFieldValue = Field(default=None, alias="7059")

    def has_market_value(self) -> bool:
        """True when the row carries at least one parseable value tag — the warm/cold test.

        A cold row carries only metadata (``conid``, ``server_id``, availability flags);
        a warm one carries last/bid/ask/size. "Parseable" is the normalizer's own parse, so
        "populated" here means "will yield an event" — a sentinel-only row counts cold.
        """
        values = (self.bid, self.ask, self.bid_size, self.ask_size, self.last, self.last_size)
        return any(value is not None for value in values)

    def spot_value(self) -> float | None:
        """The index level read: last, else bid, else ask — the first positive parsed value."""
        for value in (self.last, self.bid, self.ask):
            if value is not None and value > 0.0:
                return value
        return None


def parse_snapshot_rows(rows: object) -> tuple[SnapshotRow, ...]:
    """A snapshot/WS response body → its rows as :class:`SnapshotRow`, non-rows skipped.

    Per-row try/skip: a body that is not a list degrades to empty, an entry that is not an
    object is skipped — the same guards every snapshot consumer used to inline.
    """
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return ()
    return tuple(SnapshotRow.model_validate(row) for row in rows if isinstance(row, Mapping))


class SecdefSection(BaseModel):
    """One ``sections[]`` entry of a secdef-search row: a secType + its routing venues/months."""

    model_config = _WIRE_MODEL_CONFIG

    sec_type: WireText = Field(default="", alias="secType")
    exchange: WireText = ""
    months: WireText = ""


class SecdefSearchRow(BaseModel):
    """One ``/iserver/secdef/search`` row: symbol, conid, venue description, sections."""

    model_config = _WIRE_MODEL_CONFIG

    symbol: WireText = ""
    conid: int | None = None
    description: WireText = ""
    sec_type: WireText = Field(default="", alias="secType")
    sections: Annotated[tuple[SecdefSection, ...], BeforeValidator(keep_mappings)] = ()


def parse_secdef_search_rows(results: object) -> tuple[SecdefSearchRow, ...]:
    """A secdef-search response body → its rows, non-rows and uncoercible rows skipped.

    Per-row try/skip: a non-object entry is skipped (as always), and a row whose ``conid``
    will not coerce to an integer is skipped the same way — it can never resolve anything.
    The caller decides what an empty result means (discovery raises a labeled error).
    """
    if not isinstance(results, Sequence) or isinstance(results, (str, bytes)):
        return ()
    rows: list[SecdefSearchRow] = []
    for item in results:
        if not isinstance(item, Mapping):
            continue
        try:
            rows.append(SecdefSearchRow.model_validate(item))
        except ValidationError:
            continue
    return tuple(rows)


class StrikesPayload(BaseModel):
    """A ``/iserver/secdef/strikes`` response: call/put strike ladders (unsorted on the wire)."""

    model_config = _WIRE_MODEL_CONFIG

    call: tuple[float, ...] = ()
    put: tuple[float, ...] = ()


class SecdefInfoRow(BaseModel):
    """One ``/iserver/secdef/info`` entry: the concrete contract for one (month, strike, right).

    All four fields are required — a missing one is a malformed entry the caller rejects with
    a labeled :class:`~.cp_rest_discovery.DiscoveryError` (``ValidationError`` is a
    ``ValueError``, so the caller's existing ``except`` clause catches it).
    """

    model_config = _WIRE_MODEL_CONFIG

    conid: WireText
    maturity_date: WireText = Field(alias="maturityDate")
    strike: WireText
    right: WireText


class HistoryBarRow(BaseModel):
    """One ``marketdata/history`` bar: ``t`` epoch-ms (UTC), ``o/h/l/c`` prices, ``v`` volume.

    Every field is required and must satisfy :func:`require_bar_number` — the normalize door
    rejects a dishonest bar (missing field, bool, string-typed number, non-finite) rather than
    coercing it. Cross-field honesty (``high >= low``, open/close in range, volume >= 0) stays
    with the normalizer, which also owns the labeled error type.
    """

    model_config = _WIRE_MODEL_CONFIG

    time_ms: BarNumber = Field(alias="t")
    open_price: BarNumber = Field(alias="o")
    high: BarNumber = Field(alias="h")
    low: BarNumber = Field(alias="l")
    close: BarNumber = Field(alias="c")
    volume: BarNumber = Field(alias="v")
