"""The instrument universe: resolve broker chains, materialize masters, serve lookups.

Import the resolution pipeline (:func:`resolve_chain`, :func:`build_instrument_masters`,
:func:`materialize_universe`), the read-side :class:`UniverseService` with its four
accessors, the one chain-selection policy (:func:`plan_chain` / :func:`select_capture_keys`
over a single :class:`ChainSelection`), and the resolution/lookup errors from here.

This package carries two instrument models that coexist by design (ADR 0023). The
relocated chain-selection policy + masters above are the analytics-facing universe. The
vendored M5 instrument model (``contracts.py`` / ``discovery.py`` — :class:`Underlying`,
:class:`OptionContract`, :func:`instrument_key`, :func:`normalize_option_params`, …,
re-exported below) is what the **kept** Saxo/Deribit broker leaves import — ADR 0023 keeps
Vincent's adapters as survivors, so this is a permanent export, not a transitional one.
"""

from __future__ import annotations

from .calendar_resolver import CalendarResolver
from .chain_planning import (
    AvailableChain,
    ChainPlan,
    ChainSelection,
    DeltaBandMarket,
    TenorMarket,
    plan_chain,
    select_capture_keys,
    select_chain,
    select_expiries,
    select_strikes,
    select_strikes_delta_band,
)

# --- re-exports of the vendored M5 instrument model (kept per ADR 0023; Saxo/Deribit ride it) ---
from .contracts import (  # noqa: E402
    InstrumentKeyError,
    OptionContract,
    Right,
    Underlying,
    instrument_key,
    parse_instrument_key,
)
from .discovery import OptionParams, normalize_option_params  # noqa: E402
from .errors import (
    CalendarResolutionError,
    DuplicateBrokerContractIdError,
    IndexRegistryError,
    InstrumentMasterConflictError,
    MembershipError,
    StrikeSelectionError,
    UniverseError,
    UnknownContractError,
    UnknownInstrumentError,
    UnresolvedContractError,
)
from .index_registry import (
    IbkrRef,
    IndexEntry,
    IndexRegistry,
    parse_index_registry,
)
from .membership import (
    BasketMember,
    MembershipChange,
    basket_weight_sum,
    ingest_membership_changes,
    members,
)
from .normalization import normalize_expiry, normalize_right, resolve_contract_row
from .registry_loader import (
    enabled_indices,
    index_registry_from_config,
    load_index_registry,
)
from .service import (
    ResolvedContract,
    UniverseService,
    build_instrument_masters,
    canonical_payload,
    materialize_universe,
    resolve_chain,
)

__all__ = [
    "AvailableChain",
    "BasketMember",
    "CalendarResolutionError",
    "CalendarResolver",
    "ChainPlan",
    "ChainSelection",
    "DeltaBandMarket",
    "DuplicateBrokerContractIdError",
    "IbkrRef",
    "IndexEntry",
    "IndexRegistry",
    "IndexRegistryError",
    "InstrumentKeyError",
    "InstrumentMasterConflictError",
    "MembershipChange",
    "MembershipError",
    "OptionContract",
    "OptionParams",
    "ResolvedContract",
    "Right",
    "StrikeSelectionError",
    "TenorMarket",
    "Underlying",
    "UniverseError",
    "UniverseService",
    "UnknownContractError",
    "UnknownInstrumentError",
    "UnresolvedContractError",
    "basket_weight_sum",
    "build_instrument_masters",
    "canonical_payload",
    "enabled_indices",
    "index_registry_from_config",
    "ingest_membership_changes",
    "instrument_key",
    "load_index_registry",
    "materialize_universe",
    "members",
    "normalize_expiry",
    "normalize_option_params",
    "normalize_right",
    "parse_index_registry",
    "parse_instrument_key",
    "plan_chain",
    "resolve_chain",
    "resolve_contract_row",
    "select_capture_keys",
    "select_chain",
    "select_expiries",
    "select_strikes",
    "select_strikes_delta_band",
]
