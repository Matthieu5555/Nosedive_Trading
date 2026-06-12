"""Provider registry: the data providers the run endpoint can drive, with capabilities.

Today the platform ships one fully offline, verifiable provider (``SAMPLE``, backed by
the committed ``synthetic_known_answer`` chain fixture and driven through the exact
actor pipeline) plus declared-but-unavailable live providers. Saxo/Deribit/IBKR are
declared here and will become ``ready`` when their broker leaf packages land.

``capabilities()`` drives the UI provider selector; ``is_runnable`` gates the run endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass

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
        note="Live IBKR rides the Client-Portal REST adapter (packages/infra-ibkr); "
        "needs an authenticated CP gateway.",
    ),
    ProviderCapability(
        provider="SAXO",
        asset_class="equity",
        auth_required=True,
        data_latency="live",
        status="unavailable",
        note="OAuth2 flow wired (see /api/oauth/saxo); token exchange needs packages/infra-saxo.",
    ),
    ProviderCapability(
        provider="DERIBIT",
        asset_class="crypto",
        auth_required=False,
        data_latency="live",
        status="unavailable",
        note="Public API available; needs packages/infra-deribit.",
    ),
)

_BY_NAME = {cap.provider: cap for cap in _CAPABILITIES}


def all_capabilities() -> list[ProviderCapability]:
    """Capabilities of every known provider, sorted by name."""
    return [_BY_NAME[name] for name in sorted(_BY_NAME)]


def capability_for(provider: str) -> ProviderCapability | None:
    """The capability for ``provider`` (case-insensitive), or None if unknown."""
    return _BY_NAME.get(provider.upper())


def is_runnable(provider: str) -> bool:
    """True iff the run endpoint can actually drive a pipeline for ``provider``."""
    cap = capability_for(provider)
    return cap is not None and cap.status == "ready"
