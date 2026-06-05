"""The instrument universe: resolve broker chains, materialize masters, serve lookups.

Import the resolution pipeline (:func:`resolve_chain`, :func:`build_instrument_masters`,
:func:`materialize_universe`), the read-side :class:`UniverseService` with its four
accessors, and the resolution/lookup errors from here. The single-row resolver
:func:`resolve_contract_row` and the field normalizers are available for callers that
hold one broker row.
"""

from __future__ import annotations

from .chain_planning import (
    AvailableChain,
    ChainPlan,
    ChainSelection,
    plan_chain,
    select_chain,
    select_expiries,
    select_strikes,
)
from .errors import (
    DuplicateBrokerContractIdError,
    InstrumentMasterConflictError,
    UniverseError,
    UnknownContractError,
    UnknownInstrumentError,
    UnresolvedContractError,
)
from .normalization import normalize_expiry, normalize_right, resolve_contract_row
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
    "ChainPlan",
    "ChainSelection",
    "DuplicateBrokerContractIdError",
    "InstrumentMasterConflictError",
    "ResolvedContract",
    "UniverseError",
    "UniverseService",
    "UnknownContractError",
    "UnknownInstrumentError",
    "UnresolvedContractError",
    "build_instrument_masters",
    "canonical_payload",
    "materialize_universe",
    "normalize_expiry",
    "normalize_right",
    "plan_chain",
    "resolve_chain",
    "resolve_contract_row",
    "select_chain",
    "select_expiries",
    "select_strikes",
]
