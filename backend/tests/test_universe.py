"""Universe: deterministic resolution/dedup, strict validation, the four accessors.

Edge-case inputs (missing multiplier, missing currency) reference the shared fixture
library's named pathologies (``fixtures.get_fixture``) rather than ad-hoc literals,
per ``tasks/TESTING.md`` — they are converted to the raw broker-row shape the resolver
consumes. The determinism claim is backed by a cross-process digest, not just an
in-process re-run.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from contracts import InstrumentKey, InstrumentMaster
from fixtures import ChainFixture, get_fixture
from storage import AppendOnlyViolation, ParquetStore
from storage.serialization import to_row
from universe import (
    DuplicateBrokerContractIdError,
    InstrumentMasterConflictError,
    UniverseService,
    UnknownContractError,
    UnknownInstrumentError,
    UnresolvedContractError,
    build_instrument_masters,
    materialize_universe,
    resolve_chain,
    resolve_contract_row,
)

_SRC = Path(__file__).resolve().parents[1] / "src"
_AS_OF = date(2026, 5, 29)  # matches the fixture library's AS_OF date

# A small fixed chain used for the cross-process determinism check; kept as a literal
# so the subprocess can rebuild exactly the same input without importing test helpers.
_DETERMINISM_ROWS: list[dict[str, object]] = [
    {"conId": "u", "symbol": "AAPL", "secType": "STK", "exchange": "SMART",
     "currency": "USD", "multiplier": 1},
    {"conId": "c1", "symbol": "AAPL", "secType": "OPT", "exchange": "SMART",
     "currency": "USD", "multiplier": 100, "expiry": "20260619", "strike": 100, "right": "C"},
    {"conId": "p1", "symbol": "AAPL", "secType": "OPT", "exchange": "SMART",
     "currency": "USD", "multiplier": 100, "expiry": "20260619", "strike": 100, "right": "P"},
]


def _broker_row(instrument: InstrumentKey) -> dict[str, object]:
    """Render a canonical instrument key back into a raw broker contract row."""
    row: dict[str, object] = {
        "conId": instrument.broker_contract_id,
        "symbol": instrument.underlying_symbol,
        "secType": instrument.security_type,
        "exchange": instrument.exchange,
        "currency": instrument.currency,
        "multiplier": instrument.multiplier,
    }
    if instrument.is_option():
        assert instrument.expiry is not None and instrument.option_right is not None
        row["expiry"] = instrument.expiry.strftime("%Y%m%d")  # broker compact format
        row["strike"] = instrument.strike
        row["right"] = instrument.option_right
    return row


def _chain_rows(fixture: ChainFixture) -> list[dict[str, object]]:
    """Convert a fixture chain to the underlying + option broker rows for resolution."""
    rows = [_broker_row(fixture.underlying)]
    rows.extend(_broker_row(quote.instrument) for quote in fixture.quotes)
    return rows


def _digest(masters: tuple[InstrumentMaster, ...]) -> str:
    payload = "␞".join(f"{m.instrument_key}={m.raw_broker_payload}" for m in masters)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _storage_rows(masters: tuple[InstrumentMaster, ...]) -> list[str]:
    # default=str renders the date column, which to_row keeps as a date object for
    # Arrow rather than JSON.
    return [json.dumps(to_row(InstrumentMaster, m), sort_keys=True, default=str) for m in masters]


# -- deterministic resolution and dedup -------------------------------------


def test_resolve_chain_deduplicates_to_canonical_instruments() -> None:
    liquid = get_fixture("liquid_aapl")  # 1 underlying + 5 strikes x 2 rights = 11 rows
    resolved = resolve_chain(_chain_rows(liquid))
    assert len(resolved) == 11
    # Adding an exact duplicate option row changes nothing.
    with_dup = _chain_rows(liquid) + [_chain_rows(liquid)[1]]
    assert resolve_chain(with_dup) == resolved


def test_resolution_is_byte_identical_when_run_twice() -> None:
    rows = _chain_rows(get_fixture("liquid_aapl"))
    first = build_instrument_masters(resolve_chain(rows), _AS_OF)
    second = build_instrument_masters(resolve_chain(rows), _AS_OF)
    assert first == second
    # Byte-level: the stored rows serialize identically too.
    assert _storage_rows(first) == _storage_rows(second)


def test_resolution_is_invariant_to_broker_row_order() -> None:
    rows = _chain_rows(get_fixture("liquid_aapl"))
    canonical = build_instrument_masters(resolve_chain(rows), _AS_OF)
    shuffled = build_instrument_masters(resolve_chain(list(reversed(rows))), _AS_OF)
    assert shuffled == canonical


def test_materialized_universe_digest_is_identical_across_processes() -> None:
    # "Byte-identical" must hold across separate Python processes, not just within one
    # (the determinism rule in TESTING.md). Resolve the same chain in a fresh
    # interpreter and require an identical content digest.
    in_process = _digest(build_instrument_masters(resolve_chain(_DETERMINISM_ROWS), _AS_OF))
    code = (
        "import hashlib, json, sys;"
        "from datetime import date;"
        "from universe import resolve_chain, build_instrument_masters;"
        f"rows = json.loads({json.dumps(json.dumps(_DETERMINISM_ROWS))});"
        "masters = build_instrument_masters(resolve_chain(rows), date(2026, 5, 29));"
        "payload = '\\u241e'.join(f'{m.instrument_key}={m.raw_broker_payload}' for m in masters);"
        "print(hashlib.sha256(payload.encode('utf-8')).hexdigest())"
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONHASHSEED"}
    env["PYTHONPATH"] = str(_SRC)
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
    )
    assert result.stdout.strip() == in_process


# -- strict validation: rejected, not defaulted, not skipped ----------------


def test_missing_multiplier_is_rejected_not_defaulted() -> None:
    # The library encodes a missing multiplier as 0.0 on the instrument key.
    bad = get_fixture("missing_multiplier").quotes[0].instrument
    with pytest.raises(UnresolvedContractError) as info:
        resolve_contract_row(_broker_row(bad))
    assert info.value.field == "multiplier"


def test_missing_currency_is_rejected_not_defaulted() -> None:
    bad = get_fixture("missing_currency").quotes[0].instrument
    with pytest.raises(UnresolvedContractError) as info:
        resolve_contract_row(_broker_row(bad))
    assert info.value.field == "currency"


def test_an_unresolved_row_raises_inside_a_whole_chain_never_silently_skipped() -> None:
    # A single bad row in an otherwise good chain must explode the resolution, not be
    # dropped so the rest of the chain quietly resolves short.
    rows = _chain_rows(get_fixture("liquid_aapl"))
    rows.append({"conId": "x", "symbol": "AAPL", "secType": "OPT", "exchange": "SMART",
                 "currency": "USD", "multiplier": 100, "expiry": "not-a-date",
                 "strike": 100, "right": "C"})
    with pytest.raises(UnresolvedContractError) as info:
        resolve_chain(rows)
    assert info.value.field == "expiry"


@pytest.mark.parametrize(
    ("field", "value"),
    [("strike", "not-a-number"), ("strike", 0), ("right", "X"), ("conId", "")],
)
def test_invalid_option_fields_are_rejected(field: str, value: object) -> None:
    row = dict(_DETERMINISM_ROWS[1])
    row[field] = value
    with pytest.raises(UnresolvedContractError) as info:
        resolve_contract_row(row)
    assert info.value.field == field


# -- normalization ----------------------------------------------------------


def test_expiry_normalizes_across_broker_date_formats() -> None:
    compact = dict(_DETERMINISM_ROWS[1], expiry="20260619")
    iso = dict(_DETERMINISM_ROWS[1], expiry="2026-06-19")
    assert resolve_contract_row(compact).expiry == date(2026, 6, 19)
    assert resolve_contract_row(iso).expiry == date(2026, 6, 19)


def test_strike_is_coerced_from_string_to_float() -> None:
    row = dict(_DETERMINISM_ROWS[1], strike="100")
    strike = resolve_contract_row(row).strike
    assert strike == 100.0
    assert isinstance(strike, float)


# -- the four accessors: a hit and a miss each ------------------------------


def _service() -> UniverseService:
    resolved = resolve_chain(_chain_rows(get_fixture("liquid_aapl")))
    return UniverseService([contract.instrument for contract in resolved], _AS_OF)


def test_get_underlying_hit() -> None:
    assert _service().get_underlying("AAPL").underlying_symbol == "AAPL"


def test_get_underlying_miss_raises_with_known_symbols() -> None:
    with pytest.raises(UnknownInstrumentError) as info:
        _service().get_underlying("TSLA")
    assert info.value.symbol == "TSLA"
    assert info.value.known == ("AAPL",)


def test_get_option_chain_hit() -> None:
    chain = _service().get_option_chain("AAPL", _AS_OF)
    assert len(chain) == 10
    assert all(option.is_option() for option in chain)


def test_get_option_chain_miss_returns_empty() -> None:
    service = _service()
    assert service.get_option_chain("TSLA", _AS_OF) == ()  # unknown symbol
    assert service.get_option_chain("AAPL", date(2020, 1, 1)) == ()  # wrong session date


def test_resolve_contract_hit() -> None:
    underlying = _service().get_underlying("AAPL")
    resolved = _service().resolve_contract(underlying.broker_contract_id)
    assert resolved == underlying


def test_resolve_contract_miss_raises_with_diagnostics() -> None:
    with pytest.raises(UnknownContractError) as info:
        _service().resolve_contract("no-such-id")
    assert info.value.broker_contract_id == "no-such-id"
    assert info.value.known_count == 11


def test_load_active_universe_hit(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    materialize_universe(store, _chain_rows(get_fixture("liquid_aapl")), _AS_OF)
    loaded = UniverseService.load_active_universe(store, _AS_OF)
    assert loaded.get_underlying("AAPL").underlying_symbol == "AAPL"
    assert len(loaded.get_option_chain("AAPL", _AS_OF)) == 10


def test_load_active_universe_miss_is_empty(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    materialize_universe(store, _chain_rows(get_fixture("liquid_aapl")), _AS_OF)
    loaded = UniverseService.load_active_universe(store, date(2020, 1, 1))  # nothing that day
    assert loaded.symbols() == ()


# -- materialization --------------------------------------------------------


def test_materialize_is_idempotent_and_appends_nothing_on_rerun(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    rows = _chain_rows(get_fixture("liquid_aapl"))
    first = materialize_universe(store, rows, _AS_OF)
    on_disk_first = store.read("instrument_master")
    # Re-running must be a no-op, not an append-only collision.
    second = materialize_universe(store, rows, _AS_OF)
    on_disk_second = store.read("instrument_master")
    assert second == first
    assert len(on_disk_second) == len(on_disk_first) == 11


def test_materialize_then_read_back_round_trips(tmp_path: Path) -> None:
    store = ParquetStore(tmp_path)
    rows = _chain_rows(get_fixture("liquid_aapl"))
    materialized = materialize_universe(store, rows, _AS_OF)
    read_back = sorted(store.read("instrument_master"), key=lambda m: m.instrument_key)
    assert read_back == list(materialized)


def test_immutable_raw_layer_refuses_a_changed_payload_for_an_existing_key(
    tmp_path: Path,
) -> None:
    # An instrument materialized once is immutable: writing the same key with a
    # different verbatim payload (not via the idempotent path) is refused by the store.
    store = ParquetStore(tmp_path)
    rows = _chain_rows(get_fixture("liquid_aapl"))
    masters = materialize_universe(store, rows, _AS_OF)
    tampered = InstrumentMaster(
        instrument_key=masters[0].instrument_key,
        as_of_date=masters[0].as_of_date,
        instrument=masters[0].instrument,
        raw_broker_payload='{"tampered": true}',
    )
    with pytest.raises(AppendOnlyViolation):
        store.write("instrument_master", [tampered])


def test_materialize_rejects_changed_evidence_for_an_existing_key(tmp_path: Path) -> None:
    # Re-materializing the same instrument/date with DIFFERENT verbatim evidence is a
    # conflict surfaced loudly — not a silent no-op that returns the new payload while
    # leaving the old one on disk. (Exact idempotent rerun is covered above.)
    store = ParquetStore(tmp_path)
    base: dict[str, object] = {"conId": "u", "symbol": "AAPL", "secType": "STK",
                               "exchange": "SMART", "currency": "USD", "multiplier": 1}
    materialize_universe(store, [base], _AS_OF)
    # "description" is a non-economic field the resolver ignores: the canonical key is
    # unchanged, but the verbatim evidence payload differs.
    changed = dict(base, description="Apple Inc.")
    with pytest.raises(InstrumentMasterConflictError) as info:
        materialize_universe(store, [changed], _AS_OF)
    assert info.value.instrument_key.startswith("AAPL|STK")
    assert info.value.as_of_date == _AS_OF
    assert "description" in info.value.incoming_payload
    assert "description" not in info.value.stored_payload
    # The original evidence is untouched: the conflict surfaced, nothing was overwritten.
    on_disk = store.read("instrument_master")
    assert len(on_disk) == 1
    assert "description" not in on_disk[0].raw_broker_payload


def test_universe_rejects_duplicate_broker_contract_ids() -> None:
    # Two distinct instruments (different strikes → different canonical keys) that share
    # one broker contract id is a malformed chain; the universe refuses it loudly rather
    # than silently keeping the last one in a last-write-wins overwrite.
    shared: dict[str, object] = {"conId": "dup", "symbol": "AAPL", "secType": "OPT",
                                 "exchange": "SMART", "currency": "USD", "multiplier": 100,
                                 "expiry": "20260619", "right": "C"}
    a = resolve_contract_row(dict(shared, strike=100))
    b = resolve_contract_row(dict(shared, strike=105))
    assert a.canonical() != b.canonical()  # genuinely different instruments
    with pytest.raises(DuplicateBrokerContractIdError) as info:
        UniverseService([a, b], _AS_OF)
    assert info.value.broker_contract_id == "dup"


# -- edge cases -------------------------------------------------------------


def test_empty_chain_resolves_to_empty() -> None:
    assert resolve_chain([]) == ()
    assert build_instrument_masters((), _AS_OF) == ()


def test_single_underlying_chain_resolves() -> None:
    resolved = resolve_chain([_DETERMINISM_ROWS[0]])
    assert len(resolved) == 1
    assert not resolved[0].instrument.is_option()
