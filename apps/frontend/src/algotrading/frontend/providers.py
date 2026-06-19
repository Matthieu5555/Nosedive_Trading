from __future__ import annotations

from dataclasses import dataclass

SAMPLE_PROVIDER = "SAMPLE"


@dataclass(frozen=True, slots=True)
class ProviderCapability:

    provider: str
    asset_class: str
    auth_required: bool
    data_latency: str
    status: str
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
        status="ready",
        note="Runs the canonical end-of-day close-capture (scripts/eod_run.py) for the selected "
        "index, the same one-shot the systemd timer fires. Captures the live index and "
        "constituent option chains when the Client-Portal gateway is authenticated, else records "
        "a clean empty day. Writes to the platform store.",
    ),
)

_BY_NAME = {cap.provider: cap for cap in _CAPABILITIES}


def all_capabilities() -> list[ProviderCapability]:
    return [_BY_NAME[name] for name in sorted(_BY_NAME)]


def capability_for(provider: str) -> ProviderCapability | None:
    return _BY_NAME.get(provider.upper())


def is_runnable(provider: str) -> bool:
    cap = capability_for(provider)
    return cap is not None and cap.status == "ready"
