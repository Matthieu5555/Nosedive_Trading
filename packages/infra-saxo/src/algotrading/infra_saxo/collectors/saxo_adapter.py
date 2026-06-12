"""Saxo Bank market-data adapter: WebSocket options-chain stream → BrokerTick EAV.

Implements the ``MarketDataAdapter`` protocol from ``algotrading.infra.collectors``.
Connects to the Saxo streaming WebSocket, subscribes to the options-chain endpoint,
and translates each snapshot/delta frame into a sequence of ``BrokerTick`` events
(one per field per instrument).

Streaming model: Saxo pushes a full snapshot, then partial deltas. Snapshot strikes carry
``Strike`` (price) and a positional ``Index`` within their Expiry; deltas carry only that
``Index`` plus the changed fields. Each subscribed canonical key is parsed once at
``subscribe()`` into an exact ``(expiry, strike, right) -> key`` table; a snapshot strike
resolves through it (per expiry — never by substring-matching key text), and the resulting
``(expiry_index, strike_index) -> keys`` map routes the Index-only deltas. The strike budget
per session is config-driven (``collection.max_strikes_per_session``); PATCH pagination of
wider windows is deferred (moving the window remaps the Index basis). The ``mark_iv`` field
is read from ``Greeks.MidVolatility`` (already a decimal vol).

The WS listener runs on the shared ``WebSocketListener`` runner (owned thread, stop event,
reconnect with backoff); a reconnect restores the socket, and Saxo's
``_resetsubscriptions``/``_disconnect`` control messages still surface as feed faults so the
caller can rebuild the chain subscription when Saxo invalidates it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from algotrading.core.log import get_logger
from algotrading.infra.collectors.collector import FeedFault
from algotrading.infra.collectors.normalize import BrokerTick
from algotrading.infra.universe.contracts import (
    InstrumentKeyError,
    OptionContract,
    Right,
    parse_instrument_key,
)
from algotrading.infra_saxo.connectivity.saxo_transport import SaxoTransport
from algotrading.infra_saxo.connectivity.ws_listener import WebSocketListener

_log = get_logger(__name__)

# Saxo's per-session strike cap. This is the broker hard limit used as a fallback default; the
# real value is read from config (collection.max_strikes_per_session) and passed in by the flow.
_DEFAULT_MAX_STRIKES_PER_SESSION = 100

# Mapping from Saxo field path (within a strike's Call/Put object) to canonical EAV field_name.
# Applied for Call side; Put side uses the same field names on the Put OptionContract key.
# Implied vol is side-level (Greeks.MidVolatility), not strike-level — verified against a live
# optionschain snapshot (Expiries[].Strikes[].{Call,Put}.Greeks.MidVolatility).
_CALL_FIELD_MAP: dict[str, str] = {
    "Bid": "bid",
    "Ask": "ask",
    "LastTraded": "last",
    "OpenInterest": "open_interest",
    "Greeks.Delta": "delta",
    "Greeks.Gamma": "gamma",
    "Greeks.Vega": "vega",
    "Greeks.Theta": "theta",
    "Greeks.MidVolatility": "mark_iv",
}
_PUT_FIELD_MAP = _CALL_FIELD_MAP  # same field names, applied to Put contracts

_CONTEXT_ID = "saxo_adapter"


def _atm_start_index(strikes_sorted: list[float], spot: float, window: int) -> int:
    """``StrikeStartIndex`` that centres a ``window`` of strikes on the ATM (nearest-spot) strike.

    Saxo's per-expiry window starts at ``StrikeStartIndex`` (0 = the lowest strike). With a small
    per-expiry budget (multi-expiry), starting at 0 captures only deep-ITM strikes; the forward
    (put-call parity) and IV need the ATM region. This places the window so the ATM strike sits
    near its middle, clamped to the valid range. Pure (testable without a transport).
    """
    if not strikes_sorted or window <= 0:
        return 0
    atm = min(range(len(strikes_sorted)), key=lambda i: abs(strikes_sorted[i] - spot))
    start = atm - window // 2
    return max(0, min(start, max(0, len(strikes_sorted) - window)))


def _get_nested(obj: dict, dotted_key: str) -> float | None:
    """Traverse a dict with a dot-separated key path; return None if any segment is missing."""
    parts = dotted_key.split(".")
    cur: object = obj
    for part in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    if not isinstance(cur, (int, float, str)):
        return None
    try:
        return float(cur)
    except (TypeError, ValueError):
        return None


def parse_strike_frame(
    strike: dict,
    *,
    call_key: str,
    put_key: str,
    ts: datetime | None = None,
) -> list[BrokerTick]:
    """Pure function: one Saxo strike dict → list of BrokerTick EAV events.

    ``call_key`` and ``put_key`` are the canonical instrument keys for this strike. The strike
    carries a ``Call`` and a ``Put`` object; each side's quote, open interest and Greeks (including
    ``Greeks.MidVolatility`` as ``mark_iv``) are mapped to canonical EAV fields via
    :data:`_CALL_FIELD_MAP`. Side-level fields are emitted on their respective key.
    """
    ticks: list[BrokerTick] = []
    underlying = call_key.split(":")[1] if ":" in call_key else ""

    # Call-side fields
    call_data = strike.get("Call", {})
    for saxo_field, field_name in _CALL_FIELD_MAP.items():
        val = _get_nested(call_data, saxo_field)
        if val is not None:
            ticks.append(
                BrokerTick(
                    instrument_key=call_key,
                    field_name=field_name,
                    value=val,
                    underlying=underlying,
                    provider="SAXO",
                    exchange_ts=ts,
                )
            )

    # Put-side fields
    put_data = strike.get("Put", {})
    for saxo_field, field_name in _PUT_FIELD_MAP.items():
        val = _get_nested(put_data, saxo_field)
        if val is not None:
            ticks.append(
                BrokerTick(
                    instrument_key=put_key,
                    field_name=field_name,
                    value=val,
                    underlying=underlying,
                    provider="SAXO",
                    exchange_ts=ts,
                )
            )

    return ticks


def _iter_expiry_strikes(container: dict) -> list[tuple[int, list[dict]]]:
    """Group strike dicts by their Expiry index, tolerating both payload shapes.

    Returns ``[(expiry_index, [strike, ...]), ...]``. The snapshot nests strikes under expiries
    (``{"Expiries": [{"Index": E, "Strikes": [...]}]}``); a delta may mirror that nesting or carry
    a flat ``{"Strikes": [...]}`` (treated as expiry index 0). A ``Data`` envelope is unwrapped.
    Keeping the expiry index is required because a strike's ``Index`` is positional *within its
    Expiry*, so the snapshot-built key map must be keyed by ``(expiry_index, strike_index)``.

    The nested ``Expiries`` shape is authoritative: when present, the flat top-level ``Strikes``
    key is ignored, so a payload carrying both never emits a strike twice.
    """
    if isinstance(container.get("Data"), dict):
        container = container["Data"]
    expiries = container.get("Expiries")
    if expiries:
        return [
            (int(e.get("Index", 0)), list(e.get("Strikes") or []))
            for e in expiries
            if isinstance(e, dict)
        ]
    flat = container.get("Strikes")
    return [(0, list(flat))] if flat else []


def _parse_last_updated(obj: dict) -> datetime | None:
    """Parse a Saxo ``LastUpdated`` ISO-8601 timestamp into an aware UTC datetime, else None.

    Returns None (not an error) when the field is absent or unparseable; the caller falls back to
    capture time. This timestamp is the delayed exchange time for our delayed-data entitlement.
    """
    raw = obj.get("LastUpdated")
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def parse_stream_frame(raw: bytes) -> list[tuple[str, int, bytes]]:
    """Parse a Saxo plain-WebSocket binary frame into ``(reference_id, payload_format, payload)``.

    Saxo packs one or more messages per frame. Each message is laid out as::

        8B message-id (uint64 LE) | 2B reserved | 1B refid-length | refid (ASCII)
        | 1B payload-format (0 = UTF-8 JSON, 1 = protobuf) | 4B payload-size (uint32 LE) | payload

    Returns one tuple per complete message; the caller decodes JSON and routes by reference id.
    Truncated trailing bytes are ignored (a message may continue in a later WS frame).
    """
    messages: list[tuple[str, int, bytes]] = []
    i = 0
    n = len(raw)
    while i + 11 <= n:
        ref_len = raw[i + 10]
        ref_end = i + 11 + ref_len
        if ref_end + 5 > n:  # need refid + 1 format byte + 4 size bytes
            break
        ref_id = raw[i + 11 : ref_end].decode("ascii", errors="replace")
        fmt = raw[ref_end]
        size = int.from_bytes(raw[ref_end + 1 : ref_end + 5], "little")
        payload_start = ref_end + 5
        payload_end = payload_start + size
        if payload_end > n:
            break
        messages.append((ref_id, fmt, raw[payload_start:payload_end]))
        i = payload_end
    return messages


class SaxoMarketDataAdapter:
    """Saxo options-chain streaming adapter implementing the MarketDataAdapter protocol.

    ``instrument_keys`` passed to ``subscribe`` are expected to carry ``broker_contract_id``
    encoded as the 8th colon-segment of the canonical key, or the adapter builds the
    subscription from the Uic encoded there. In practice, the caller builds instrument keys
    via ``instrument_key(contract)`` where ``contract.broker_contract_id`` holds the Saxo Uic.
    """

    def __init__(
        self,
        transport: SaxoTransport,
        *,
        asset_type: str = "StockOption",
        n_expiries: int = 1,
        max_strikes_per_session: int = _DEFAULT_MAX_STRIKES_PER_SESSION,
    ) -> None:
        self._transport = transport
        # OptionsChain subscriptions are typed: an equity name is "StockOption", an ETF
        # (e.g. SPY) is "EtfOption". Must match the discovered instrument or Saxo returns
        # an empty/errored subscription — never hardcode it to one type.
        self._asset_type = asset_type
        # Number of nearest expiry windows to stream. A single smile (n=1) is enough to validate
        # the feed, but a surface needs several maturities. Each expiry is a separate window of
        # <=max_strikes_per_session strikes; deltas route per (expiry_index, strike_index).
        self._n_expiries = max(1, n_expiries)
        # Total strikes streamed per session, read from config (the flow passes it); the default
        # is only the broker hard limit. Never a constant in this file — it's a config rule.
        self._max_strikes_per_session = max(1, max_strikes_per_session)
        # Optional reference spot: when set, each expiry window is ATM-centred (StrikeStartIndex)
        # instead of starting at the lowest strike — needed for a usable multi-expiry surface.
        self._reference_spot: float | None = None
        self._tick_cb: Callable[[BrokerTick], None] | None = None
        self._fault_cb: Callable[[FeedFault], None] | None = None
        self._reference_id: str = "chain1"
        self._subscribed_keys: list[str] = []
        # Exact routing tables, built once per subscribe() by parsing each canonical key with
        # the canonical parser. A snapshot strike is resolved by (expiry, strike, right) — never
        # by substring-matching the key text, which misrouted strikes across expiries and
        # false-matched the multiplier segment (e.g. ':100:' hit every multiplier-100 key).
        self._keys_by_contract: dict[tuple[date, Decimal, Right], str] = {}
        # Saxo's per-window Expiry Index i maps to our i-th *sorted* subscribed expiry — the
        # same nearest-first ordering assumption _expiry_windows builds the subscription with.
        self._expiry_order: list[date] = []
        # (expiry_index, strike_index) -> (call_key, put_key), built from snapshot strikes so that
        # later partial deltas (which identify a strike only by its positional Index) can be routed.
        self._index_map: dict[tuple[int, int], tuple[str, str]] = {}
        self._unknown_indices: set[tuple[int, int]] = set()
        self._snapshot_no_index: set[tuple[int, str]] = set()
        self._listener: WebSocketListener | None = None

    def set_reference_spot(self, spot: float) -> None:
        """Set the underlying spot so expiry windows are ATM-centred (call before ``subscribe``)."""
        self._reference_spot = spot

    def _expiry_windows(self) -> list[dict]:
        """Build the per-expiry ``{Index, StrikeStartIndex}`` list for the subscription Arguments.

        Without a reference spot, every window starts at 0 (lowest strikes). With a spot, each
        window is ATM-centred from the subscribed keys' strikes for that expiry. NB the mapping
        our-i-th-expiry -> Saxo Expiry Index i assumes discovery's expiry order matches Saxo's
        (nearest first); that one assumption needs a live EU validation (Saxo's Index basis has
        surprised us before — see the streaming delta-Index fix).
        """
        per_expiry = max(1, self._max_strikes_per_session // self._n_expiries)
        if self._reference_spot is None:
            return [{"Index": i, "StrikeStartIndex": 0} for i in range(self._n_expiries)]
        strikes_by_expiry: dict[date, set[Decimal]] = {}
        for expiry, strike, _right in self._keys_by_contract:
            strikes_by_expiry.setdefault(expiry, set()).add(strike)
        expiries_sorted = sorted(strikes_by_expiry)[: self._n_expiries]
        windows: list[dict] = []
        for i, expiry in enumerate(expiries_sorted):
            strikes = sorted(float(s) for s in strikes_by_expiry[expiry])
            start = _atm_start_index(strikes, self._reference_spot, per_expiry)
            windows.append({"Index": i, "StrikeStartIndex": start})
        # Cover the remaining requested windows (no strikes discovered) from 0.
        for i in range(len(windows), self._n_expiries):
            windows.append({"Index": i, "StrikeStartIndex": 0})
        return windows

    def set_tick_callback(self, callback: Callable[[BrokerTick], None]) -> None:
        self._tick_cb = callback

    def set_fault_callback(self, callback: Callable[[FeedFault], None]) -> None:
        self._fault_cb = callback

    def subscribe(self, instrument_keys: Sequence[str]) -> None:
        """Open a streaming subscription for the given canonical instrument keys.

        Extracts the underlying Uic from the first key (all keys must share one underlying).
        Saxo's options-chain subscription is per-underlying, not per-strike.
        """
        self._subscribed_keys = list(instrument_keys)
        self._rebuild_key_lookup()
        self._index_map.clear()
        self._unknown_indices.clear()
        self._snapshot_no_index.clear()
        if not instrument_keys:
            return

        # Saxo streams at most max_strikes_per_session strikes total, split across the requested
        # expiry windows (ATM-centred per expiry when a reference spot is set; see _expiry_windows).
        # Surface the cap rather than truncating silently.
        if len(instrument_keys) > self._max_strikes_per_session * 2:  # 2 keys (Call+Put) per strike
            _log.warning(
                "Saxo options-chain window capped: %d instrument keys provided but the "
                "subscription streams <=%d strikes total across %d expiry window(s); rest dropped.",
                len(instrument_keys),
                self._max_strikes_per_session,
                self._n_expiries,
            )

        # Derive the underlying Uic from the first instrument_key's broker_contract_id segment.
        # Canonical key format: TYPE:SYMBOL:SEC_TYPE:EXPIRY:RIGHT:STRIKE:MULT:EXCHANGE:CCY
        # broker_contract_id is stored in the raw field; we encode it in the exchange segment
        # using the pattern SAXO_{uic} so the key stays parseable. Fall back to symbol-based lookup.
        uic = self._extract_uic(instrument_keys[0])

        body = {
            "ContextId": _CONTEXT_ID,
            "ReferenceId": self._reference_id,
            "Arguments": {
                "AssetType": self._asset_type,
                "Identifier": uic,
                # Saxo caps the TOTAL strikes per streaming session (~100), not per expiry, so split
                # the budget across the requested expiry windows (else multi-expiry => 400 error).
                "MaxStrikesPerExpiry": max(1, self._max_strikes_per_session // self._n_expiries),
                # ATM-centred per expiry when a reference spot is set (else lowest strikes).
                "Expiries": self._expiry_windows(),
            },
        }
        response = self._transport.post("/trade/v1/optionschain/subscriptions", body)
        # The subscription POST returns the initial full chain inline as ``Snapshot``; emit it so
        # the first image is not lost (the WS then streams deltas). Without it, only later deltas
        # would surface — and a quiet/delayed chain may send none within a short window.
        if isinstance(response, dict) and isinstance(response.get("Snapshot"), dict):
            self._handle_payload(response["Snapshot"])
        self._start_ws_listener()

    def unsubscribe_all(self) -> None:
        """Cancel the active streaming subscription and stop the WS listener thread."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        try:
            self._transport.delete(
                f"/trade/v1/optionschain/subscriptions/{_CONTEXT_ID}/{self._reference_id}"
            )
        except Exception:  # noqa: BLE001 — SaxoTransportError and network errors are heterogeneous
            _log.exception("Error cancelling Saxo subscription")
        self._subscribed_keys = []
        self._keys_by_contract.clear()
        self._expiry_order = []
        self._index_map.clear()
        self._unknown_indices.clear()
        self._snapshot_no_index.clear()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_uic(instrument_key: str) -> int:
        """Extract the Saxo Uic from a canonical instrument key's exchange segment (SAXO_<uic>).

        Uses the canonical key parser (``parse_instrument_key``); its ``InstrumentKeyError`` is a
        ``ValueError``, so malformed keys raise the same class as a missing ``SAXO_`` segment.
        """
        contract = parse_instrument_key(instrument_key)
        if contract.exchange.startswith("SAXO_"):
            try:
                return int(contract.exchange[5:])
            except ValueError:
                pass
        raise ValueError(f"Cannot extract Saxo Uic from instrument key: {instrument_key!r}")

    def _rebuild_key_lookup(self) -> None:
        """Parse each subscribed key once into the exact ``(expiry, strike, right) -> key`` table.

        A key that is not a parseable canonical option key cannot be routed and is skipped with a
        warning (it would previously have been substring-matched, which is how strikes leaked
        across expiries and multipliers).
        """
        self._keys_by_contract.clear()
        for key in self._subscribed_keys:
            try:
                contract = parse_instrument_key(key)
            except InstrumentKeyError:
                _log.warning("Subscribed key %r is not a canonical option key; skipping.", key)
                continue
            if not isinstance(contract, OptionContract):
                _log.warning("Subscribed key %r is not an option key; skipping.", key)
                continue
            self._keys_by_contract[(contract.expiry, contract.strike, contract.right)] = key
        self._expiry_order = sorted({expiry for expiry, _, _ in self._keys_by_contract})

    def _start_ws_listener(self) -> None:
        def _connect_factory() -> object:
            # Imported lazily — only needed when streaming is active. The factory runs per
            # (re)start, so the handshake always carries the *current* Bearer token.
            import websockets

            return websockets.connect(
                self._transport.streaming_url(_CONTEXT_ID),
                additional_headers=self._transport.auth_header(),
            )

        self._listener = WebSocketListener(
            connect_factory=_connect_factory,
            on_frame=self._handle_frame,
            on_fault=self._emit_fault,
            name="saxo-ws-listener",
        )
        self._listener.start()

    def _handle_frame(self, raw: bytes | str) -> None:
        """Decode a Saxo binary streaming frame and emit BrokerTick events.

        Routes each packed message by reference id: control messages (``_heartbeat`` etc.)
        are handled separately; our subscription's JSON payloads are parsed into ticks.
        """
        data = raw if isinstance(raw, bytes) else raw.encode("utf-8")
        for ref_id, fmt, payload in parse_stream_frame(data):
            if ref_id.startswith("_"):
                self._handle_control(ref_id)
                continue
            if ref_id != self._reference_id:
                continue
            if fmt != 0:
                self._emit_fault(f"Unsupported payload format {fmt} (protobuf) for {ref_id}")
                continue
            try:
                msg = json.loads(payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                self._emit_fault(f"Malformed JSON payload for {ref_id}: {exc}")
                continue
            self._handle_payload(msg)

    def _handle_control(self, ref_id: str) -> None:
        """Handle Saxo control messages. Heartbeat is benign; reset/disconnect invalidate state."""
        if ref_id == "_heartbeat":
            return
        # _resetsubscriptions / _disconnect mean our subscription state can no longer be trusted.
        self._emit_fault(f"Saxo streaming control message: {ref_id}")

    def _handle_payload(self, msg: dict) -> None:
        """Emit ticks from one option-chain subscription payload (snapshot or delta).

        The reference id lives in the frame header, so the payload is the data object itself.
        Snapshot strikes (carrying ``Strike``) populate the ``Index -> keys`` map; delta strikes
        (carrying only ``Index``) are routed through it. ``exchange_ts`` comes from the payload's
        (or strike's) ``LastUpdated`` — the delayed exchange time — falling back to capture time.
        """
        payload_ts = _parse_last_updated(msg)
        fallback_ts = datetime.now(UTC)

        for expiry_index, strikes in _iter_expiry_strikes(msg):
            for strike in strikes:
                keys = self._resolve_strike_keys(expiry_index, strike)
                if keys is None:
                    continue
                call_key, put_key = keys
                ts = _parse_last_updated(strike) or payload_ts or fallback_ts
                ticks = parse_strike_frame(strike, call_key=call_key, put_key=put_key, ts=ts)
                if self._tick_cb:
                    for tick in ticks:
                        self._tick_cb(tick)

    def _resolve_strike_keys(self, expiry_index: int, strike: dict) -> tuple[str, str] | None:
        """Resolve a strike's (call_key, put_key), populating/consulting the Index map.

        A snapshot strike carries ``Strike`` (price): resolve by price and record the mapping
        ``(expiry_index, strike Index) -> keys``. A delta strike carries only ``Index``: look it
        up in the map. An unknown delta Index is logged once (not silently dropped).
        """
        strike_price = strike.get("Strike")
        if strike_price is not None:
            try:
                keys = self._keys_for_strike(expiry_index, strike_price)
            except ValueError:
                # Strike outside our subscribed key set — expected filtering. The map write below
                # is skipped, so no delta is expected for this strike either.
                return None
            idx = strike.get("Index")
            if idx is not None:
                self._index_map[(expiry_index, int(idx))] = keys
            else:
                # No Index on a snapshot strike → its deltas (Index-only) cannot be routed. Surface
                # it here so the misleading "Index basis shifted" warning is not the only signal.
                self._warn_snapshot_missing_index(expiry_index, strike_price)
            return keys

        idx = strike.get("Index")
        if idx is None:
            return None
        mapped = self._index_map.get((expiry_index, int(idx)))
        if mapped is None:
            self._note_unmapped_index(expiry_index, int(idx))
        return mapped

    def _note_unmapped_index(self, expiry_index: int, strike_index: int) -> None:
        """Record (once) a delta whose strike Index is not in the snapshot map, and skip it.

        Saxo streams the full expiry chain, but we subscribe a sub-window; deltas for strikes
        outside that window have no key and are expected to be dropped — so this is debug, not a
        warning (the sub-window is already surfaced by the truncation warning at subscribe time).
        """
        marker = (expiry_index, strike_index)
        if marker in self._unknown_indices:
            return
        self._unknown_indices.add(marker)
        _log.debug(
            "Saxo delta strike Index %d (expiry %d) is outside the subscribed window; skipping.",
            strike_index,
            expiry_index,
        )

    def _warn_snapshot_missing_index(self, expiry_index: int, strike_price: float) -> None:
        """Log once per snapshot strike that lacks an Index — its deltas cannot be routed."""
        marker = (expiry_index, str(strike_price))
        if marker in self._snapshot_no_index:
            return
        self._snapshot_no_index.add(marker)
        _log.warning(
            "Saxo snapshot strike (price=%s, expiry=%d) has no Index field; its Index-only deltas "
            "cannot be routed and will be dropped.",
            strike_price,
            expiry_index,
        )

    def _keys_for_strike(self, expiry_index: int, strike_price: float) -> tuple[str, str]:
        """Exact lookup of the (call_key, put_key) for one strike *within its expiry*.

        ``expiry_index`` is Saxo's positional Expiry Index, mapped to the expiry date through
        ``_expiry_order`` (the same sorted-ascending order ``_expiry_windows`` built the
        subscription with). The strike is compared as a ``Decimal``, so ``530.0`` matches a key
        carrying canonical strike ``530`` — and never matches a multiplier or another expiry's
        strike, which the old substring scan did.
        """
        if not 0 <= expiry_index < len(self._expiry_order):
            raise ValueError(f"Expiry index {expiry_index} outside the subscribed expiries")
        expiry = self._expiry_order[expiry_index]
        try:
            strike = Decimal(str(strike_price))
        except InvalidOperation as exc:
            raise ValueError(f"Unparseable strike {strike_price!r}") from exc
        call_key = self._keys_by_contract.get((expiry, strike, Right.CALL))
        put_key = self._keys_by_contract.get((expiry, strike, Right.PUT))
        if call_key is None or put_key is None:
            raise ValueError(f"No keys found for strike {strike_price} in expiry {expiry}")
        return call_key, put_key

    def _emit_fault(self, reason: str) -> None:
        if self._fault_cb:
            self._fault_cb(
                FeedFault(
                    kind="other",
                    code=None,
                    message=reason,
                    instrument_key=None,
                )
            )
        else:
            _log.warning("Saxo feed fault (no callback): %s", reason)
