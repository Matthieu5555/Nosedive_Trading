"""Provider registry: the data providers the run endpoint can drive, with capabilities.

The registry lives in the app — the only layer that may name a broker. Today the flat
backend ships one fully offline, verifiable provider (``SAMPLE``, backed by the committed
``synthetic_known_answer`` chain fixture and driven through the exact actor pipeline) plus
the live ``IBKR`` provider, declared but reported ``unavailable`` because it needs the
optional ``ib_async`` dependency and a running gateway. Saxo/Deribit are not in the flat
backend yet (they arrive with ``packages/infra-saxo`` / ``-deribit`` under the restructure),
so they are intentionally absent here rather than faked.

``capabilities()`` drives the UI provider selector; ``is_runnable`` gates the run endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

# The one offline provider that produces a real, persisted surface with no network.
SAMPLE_PROVIDER = "SAMPLE"


@dataclass(frozen=True, slots=True)
class ProviderCapability:
    """What a provider offers — the operator-facing selector row."""

    provider: str
    asset_class: str
    auth_required: bool
    data_latency: str
    status: str  # "ready" | "unavailable"
    note: str

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "asset_class": self.asset_class,
            "auth_required": self.auth_required,
            "data_latency": self.data_latency,
            "status": self.status,
            "note": self.note,
        }


# Hardcoded at module top (with the reason): the providers wireable from the flat backend.
_CAPABILITIES: tuple[ProviderCapability, ...] = (
    ProviderCapability(
        provider=SAMPLE_PROVIDER,
        asset_class="equity",
        auth_required=False,
        data_latency="offline",
        status="ready",
        note="Offline synthetic chain fixture; runs the full actor pipeline with no network.",
    ),
    ProviderCapability(
        provider="IBKR",
        asset_class="equity",
        auth_required=False,
        data_latency="delayed",
        status="unavailable",
        note="Needs the optional 'ib_async' dependency and a running IB gateway; not driven here.",
    ),
)

_BY_NAME = {capability.provider: capability for capability in _CAPABILITIES}


def all_capabilities() -> list[ProviderCapability]:
    """Capabilities of every known provider, sorted by name — drives the UI selector."""
    return [_BY_NAME[name] for name in sorted(_BY_NAME)]


def capability_for(provider: str) -> ProviderCapability | None:
    """The capability for ``provider`` (case-insensitive), or None if unknown."""
    return _BY_NAME.get(provider.upper())


def is_runnable(provider: str) -> bool:
    """True iff the run endpoint can actually drive a pipeline for ``provider``."""
    capability = capability_for(provider)
    return capability is not None and capability.status == "ready"
