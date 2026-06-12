"""1J — the typed index registry: round-trip, per-entry validation, the enabled filter.

The registry says *which indices the platform tracks* (ADR 0035). These tests pin:

* a well-formed ``indices:`` block parses to the expected typed entries, asserted against a
  hand-written fixture (symbols, calendar codes, enabled flags) — never read back from the
  parser;
* each malformed entry is rejected with a *labeled* :class:`IndexRegistryError`, never
  coerced — the load-bearing negative is an **unknown calendar code**, which must NOT
  silently default to some calendar (a wrong calendar = a wrong close instant = look-ahead);
* the enabled filter exposes *only* the enabled set through the seam accessor;
* the block is hashed inside the ``universe`` bundle with no separate hash;
* the edge cases TESTING.md floors: empty block, duplicate symbol.

Expected values (symbols, calendar codes, flags) are hand-encoded here as the independent
oracle; the parser output is checked against them, not the reverse.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from algotrading.core.config import config_hashes, load_platform_config
from algotrading.infra.universe import (
    IndexRegistryError,
    enabled_indices,
    index_registry_from_config,
    load_index_registry,
    parse_index_registry,
)

CONFIGS_DIR = Path(__file__).resolve().parents[3] / "configs"


# --- a hand-written well-formed block (the oracle) -------------------------------------
def _well_formed_block() -> dict[str, object]:
    return {
        "SX5E": {
            "name": "EURO STOXX 50",
            "calendar": "XEUR",
            "currency": "EUR",
            "ibkr": {"conid": 11920371, "secType": "IND", "exchange": "EUREX"},
            "enabled": True,
        },
        "SPX": {
            "name": "S&P 500",
            "calendar": "XNYS",
            "currency": "USD",
            "ibkr": {"conid": 416904, "secType": "IND", "exchange": "CBOE"},
            "enabled": False,
        },
    }


def test_well_formed_block_parses_to_the_expected_typed_entries() -> None:
    registry = parse_index_registry(_well_formed_block())
    # Sorted-symbol order is canonical: SPX before SX5E.
    assert [e.symbol for e in registry.entries] == ["SPX", "SX5E"]

    sx5e = registry.get("SX5E")
    assert sx5e.name == "EURO STOXX 50"
    assert sx5e.calendar == "XEUR"
    assert sx5e.currency == "EUR"
    assert sx5e.enabled is True
    assert sx5e.ibkr.conid == 11920371
    assert sx5e.ibkr.sec_type == "IND"
    assert sx5e.ibkr.exchange == "EUREX"

    spx = registry.get("SPX")
    assert spx.calendar == "XNYS"
    assert spx.currency == "USD"
    assert spx.enabled is False


def test_empty_block_is_a_valid_empty_registry_not_a_crash() -> None:
    assert parse_index_registry({}).entries == ()
    assert parse_index_registry(None).entries == ()
    assert enabled_indices(parse_index_registry({})) == ()


# --- the enabled filter ----------------------------------------------------------------
def test_enabled_filter_exposes_only_the_enabled_set() -> None:
    block = _well_formed_block()  # SX5E enabled, SPX disabled
    registry = parse_index_registry(block)
    enabled = enabled_indices(registry)
    assert [e.symbol for e in enabled] == ["SX5E"]
    # A disabled index is absent from the seam and so never reaches capture.
    assert "SPX" not in {e.symbol for e in enabled}


def test_enabled_filter_is_empty_when_all_disabled() -> None:
    block = _well_formed_block()
    block["SX5E"]["enabled"] = False  # type: ignore[index]
    assert enabled_indices(parse_index_registry(block)) == ()


# --- negative paths: labeled rejection, never coercion ---------------------------------
def _block_with(symbol: str, **overrides: object) -> dict[str, object]:
    entry = {
        "name": "X",
        "calendar": "XNYS",
        "currency": "USD",
        "ibkr": {"conid": 1, "secType": "IND", "exchange": "CBOE"},
        "enabled": True,
    }
    entry.update(overrides)
    return {symbol: entry}


def test_unknown_calendar_code_is_rejected_and_never_defaulted() -> None:
    # The load-bearing negative: a typo like XEURX must NOT fall back to a default calendar.
    with pytest.raises(IndexRegistryError) as exc:
        parse_index_registry(_block_with("SPX", calendar="XEURX"))
    assert exc.value.symbol == "SPX"
    assert exc.value.field == "calendar"
    assert exc.value.value == "XEURX"
    assert "never defaulted" in exc.value.reason


def test_empty_symbol_is_rejected() -> None:
    with pytest.raises(IndexRegistryError) as exc:
        parse_index_registry(_block_with("   "))
    assert exc.value.field == "symbol"


def test_bad_currency_is_rejected() -> None:
    for bad in ("US", "usd", "DOLLAR", "U$D"):
        with pytest.raises(IndexRegistryError) as exc:
            parse_index_registry(_block_with("SPX", currency=bad))
        assert exc.value.field == "currency", bad


def test_non_bool_enabled_is_rejected_not_coerced() -> None:
    # "true"/1 are truthy but not bools — coercing them is exactly the silent-default bug.
    for bad in ("true", 1, 0, None):
        with pytest.raises(IndexRegistryError) as exc:
            parse_index_registry(_block_with("SPX", enabled=bad))
        assert exc.value.field == "enabled", bad


def test_missing_field_is_rejected() -> None:
    entry = {
        "name": "X",
        "calendar": "XNYS",
        "currency": "USD",
        "ibkr": {"conid": 1, "secType": "IND", "exchange": "CBOE"},
        # enabled missing
    }
    with pytest.raises(IndexRegistryError) as exc:
        parse_index_registry({"SPX": entry})
    assert exc.value.field == "enabled"
    assert "missing" in exc.value.reason


def test_unknown_key_in_entry_is_rejected() -> None:
    with pytest.raises(IndexRegistryError) as exc:
        parse_index_registry(_block_with("SPX", typo_field="oops"))
    assert exc.value.field == "typo_field"


def test_bad_ibkr_subblock_is_rejected() -> None:
    # conid must be an int; a bool (YAML true) is not a conid.
    with pytest.raises(IndexRegistryError) as exc:
        parse_index_registry(
            _block_with("SPX", ibkr={"conid": True, "secType": "IND", "exchange": "CBOE"})
        )
    assert exc.value.field == "ibkr.conid"
    # empty exchange is rejected
    with pytest.raises(IndexRegistryError) as exc:
        parse_index_registry(
            _block_with("SPX", ibkr={"conid": 1, "secType": "IND", "exchange": ""})
        )
    assert exc.value.field == "ibkr.exchange"


def test_ibkr_symbol_override_is_parsed_and_drives_search_symbol() -> None:
    """``ibkr.symbol`` is the IBKR secdef ticker override; the registry key stays the vocabulary.

    Euro Stoxx 50 lists on IBKR as ``ESTX50``, not ``SX5E`` — the override carries that, while
    ``symbol`` (the platform-wide key) is unchanged. ``ibkr_search_symbol`` prefers the override and
    falls back to the key when absent (an entry that needs no override is untouched).
    """
    block = {
        "SX5E": {
            "name": "EURO STOXX 50", "calendar": "XEUR", "currency": "EUR",
            "ibkr": {"conid": 4356500, "secType": "IND", "exchange": "EUREX", "symbol": "ESTX50"},
            "enabled": True,
        },
        "SPX": {
            "name": "S&P 500", "calendar": "XNYS", "currency": "USD",
            "ibkr": {"conid": 416904, "secType": "IND", "exchange": "CBOE"},  # no override needed
            "enabled": True,
        },
    }
    registry = parse_index_registry(block)
    sx5e, spx = registry.get("SX5E"), registry.get("SPX")
    assert sx5e.ibkr.symbol == "ESTX50"
    assert sx5e.ibkr_search_symbol == "ESTX50"  # resolution uses the IBKR ticker
    assert sx5e.symbol == "SX5E"  # the platform vocabulary is unchanged
    assert spx.ibkr.symbol is None
    assert spx.ibkr_search_symbol == "SPX"  # no override → the registry key


def test_constituent_conid_pins_are_parsed_into_ordered_pairs() -> None:
    """``ibkr.constituent_conids`` is the verified-conid pin map for unsearchable constituents.

    The Euro Stoxx 50 carries two members on ticker ``SAN`` (Santander/Madrid, Sanofi/Paris) — a
    bare-symbol search can resolve only one, so the other is pinned by its unique conid under a
    distinct label. Parsed to ``(label, conid)`` pairs; an entry with no pins defaults to empty.
    """
    block = {
        "SX5E": {
            "name": "EURO STOXX 50", "calendar": "XEUR", "currency": "EUR",
            "ibkr": {
                "conid": 4356500, "secType": "IND", "exchange": "EUREX", "symbol": "ESTX50",
                "constituent_conids": {"SAN1": 29612249},
            },
            "enabled": True,
        },
        "SPX": {
            "name": "S&P 500", "calendar": "XNYS", "currency": "USD",
            "ibkr": {"conid": 416904, "secType": "IND", "exchange": "CBOE"},  # no pins
            "enabled": True,
        },
    }
    registry = parse_index_registry(block)
    assert registry.get("SX5E").ibkr.constituent_conids == (("SAN1", 29612249),)
    assert registry.get("SPX").ibkr.constituent_conids == ()  # default empty, not a crash


def test_bad_constituent_conid_pin_is_rejected_not_coerced() -> None:
    """A non-int / zero / negative pinned conid is a labeled error — a pin must name a real contract."""
    for bad in (True, 0, -3, "29612249"):
        with pytest.raises(IndexRegistryError) as exc:
            parse_index_registry(
                _block_with(
                    "SPX",
                    ibkr={
                        "conid": 1, "secType": "IND", "exchange": "CBOE",
                        "constituent_conids": {"SAN1": bad},
                    },
                )
            )
        assert exc.value.field == "ibkr.constituent_conids.SAN1", bad
    # the map itself must be a mapping, not a list
    with pytest.raises(IndexRegistryError) as exc:
        parse_index_registry(
            _block_with(
                "SPX",
                ibkr={"conid": 1, "secType": "IND", "exchange": "CBOE", "constituent_conids": ["x"]},
            )
        )
    assert exc.value.field == "ibkr.constituent_conids"


def test_blank_ibkr_symbol_override_is_rejected() -> None:
    """A present-but-blank ``ibkr.symbol`` is an operator error, not a clean 'no override'."""
    with pytest.raises(IndexRegistryError) as exc:
        parse_index_registry(
            _block_with("SPX", ibkr={"conid": 1, "secType": "IND", "exchange": "CBOE", "symbol": " "})
        )
    assert exc.value.field == "ibkr.symbol"


def test_duplicate_symbol_after_normalisation_is_rejected() -> None:
    # YAML keys are unique, but a registry built in memory from a list could repeat; the
    # frozen registry refuses a duplicate symbol rather than silently keeping the last.
    from algotrading.infra.universe import IbkrRef, IndexEntry, IndexRegistry

    entry = IndexEntry(
        symbol="SPX",
        name="S&P 500",
        calendar="XNYS",
        currency="USD",
        ibkr=IbkrRef(conid=1, sec_type="IND", exchange="CBOE"),
        enabled=True,
    )
    with pytest.raises(IndexRegistryError):
        IndexRegistry(entries=(entry, entry))


def test_get_unknown_index_raises_labeled_error() -> None:
    registry = parse_index_registry(_well_formed_block())
    with pytest.raises(IndexRegistryError) as exc:
        registry.get("NDX")
    assert exc.value.symbol == "NDX"


# --- the seam from the real config + hash discipline -----------------------------------
def test_loads_from_real_universe_yaml() -> None:
    registry = load_index_registry(CONFIGS_DIR)
    symbols = {e.symbol for e in registry.entries}
    assert {"SX5E", "SPX"} <= symbols
    # Both seed indices are now enabled (the live EOD spine surfaces them); the calendar/
    # projection path uses only the symbol/calendar (the conids, now verified-live, are consumed
    # only at the IBKR door). The enabled set is exactly the two seeds, in canonical (sorted) order.
    assert [e.symbol for e in enabled_indices(registry)] == ["SPX", "SX5E"]
    # The canonical symbol is SPX (never SP500) per the spec audit.
    assert "SP500" not in symbols


def test_index_block_is_in_the_universe_hash_with_no_separate_hash() -> None:
    config = load_platform_config(CONFIGS_DIR)
    bundles = set(config_hashes(config))
    # No "indices" bundle key is introduced — it travels inside "universe".
    assert "indices" not in bundles
    assert "universe" in bundles


def test_changing_an_index_moves_only_the_universe_hash() -> None:
    config = load_platform_config(CONFIGS_DIR)
    before = config_hashes(config)
    # Flip an enabled flag in the raw block: an economic change (changes which records exist).
    # Both seeds now ship enabled, so flip SPX OFF to register a genuine change.
    new_indices = {
        symbol: ({**dict(entry), "enabled": False} if symbol == "SPX" else dict(entry))
        for symbol, entry in config.universe.indices.items()
    }
    moved_universe = config.universe.model_copy(update={"indices": new_indices})
    moved = config.model_copy(update={"universe": moved_universe})
    after = config_hashes(moved)
    assert after["universe"] != before["universe"]
    for bundle in ("qc", "pricing", "scenarios"):
        assert after[bundle] == before[bundle]


def test_index_registry_from_config_matches_direct_parse() -> None:
    config = load_platform_config(CONFIGS_DIR)
    from_cfg = index_registry_from_config(config)
    direct = parse_index_registry(config.universe.indices)
    assert [e.symbol for e in from_cfg.entries] == [e.symbol for e in direct.entries]
