"""The instrument universe: resolve broker chains, materialize masters, serve lookups.

Import the resolution pipeline (:func:`resolve_chain`, :func:`build_instrument_masters`,
:func:`materialize_universe`), the read-side :class:`UniverseService` with its four
accessors, the one chain-selection policy (:func:`plan_chain` / :func:`select_capture_keys`
over a single :class:`ChainSelection`), and the resolution/lookup errors from here.

TRANSITIONAL (C1 commit 1): the forked M5 instrument model (``contracts.py`` /
``discovery.py`` — :class:`Underlying`, :class:`OptionContract`, :func:`instrument_key`,
:func:`normalize_option_params`, …) still lives here because the broker leaves import it.
It is removed and the leaves retargeted onto the one policy (discovery emits
:class:`AvailableChain` for :func:`plan_chain`) in C1 commit 2 (see ADR 0023). Until then
this package exports the union so the gate stays green.
"""

from __future__ import annotations

from .chain_planning import (
    AvailableChain,
    ChainPlan,
    ChainSelection,
    plan_chain,
    select_capture_keys,
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

# --- transitional re-exports of the forked M5 instrument model (removed in C1 commit 2) ---
from .contracts import (  # noqa: E402
    InstrumentKeyError,
    OptionContract,
    Right,
    Underlying,
    instrument_key,
    parse_instrument_key,
)
from .discovery import OptionParams, normalize_option_params  # noqa: E402

__all__ = [
    "AvailableChain",
    "ChainPlan",
    "ChainSelection",
    "DuplicateBrokerContractIdError",
    "InstrumentKeyError",
    "InstrumentMasterConflictError",
    "OptionContract",
    "OptionParams",
    "ResolvedContract",
    "Right",
    "Underlying",
    "UniverseError",
    "UniverseService",
    "UnknownContractError",
    "UnknownInstrumentError",
    "UnresolvedContractError",
    "build_instrument_masters",
    "canonical_payload",
    "instrument_key",
    "materialize_universe",
    "normalize_expiry",
    "normalize_option_params",
    "normalize_right",
    "parse_instrument_key",
    "plan_chain",
    "resolve_chain",
    "resolve_contract_row",
    "select_capture_keys",
    "select_chain",
    "select_expiries",
    "select_strikes",
]
