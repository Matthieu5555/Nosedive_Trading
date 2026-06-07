"""IBKR historical-fetch configuration — typed, validated, no-hardcode (ADR 0031 / C7).

The unattended backfill needs hosts/URLs, timeouts, the 5-concurrent cap, the established-
session wait, and the maintenance-window retry/backoff. None of those are economic (they do
not change *what* is computed), so they are not a hashed bundle — but they are still config,
not ``.py`` literals (the C7 discipline the 1C spec carries forward). The canonical defaults
live in the versioned ``configs/ibkr_history.yaml`` beside the package; this module loads and
validates them into a frozen :class:`IbkrHistoryConfig`.

Secrets never pass through here: the OAuth consumer key/secret and the Live Session Token are
read from ``.env`` by the caller and handed to the signer; this object carries only the
non-secret connectivity knobs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from algotrading.core.config import LoadedConfig, load_yaml_config

# configs/ibkr_history.yaml sits beside src/: src/algotrading/infra_ibkr/config.py
# parents[3] == packages/infra-ibkr
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "ibkr_history.yaml"


class IbkrHistoryConfigError(Exception):
    """A field in the IBKR history config is missing or malformed — labeled, never silent."""


@dataclass(frozen=True, slots=True)
class EstablishedWaitConfig:
    """How long to wait for the brokerage session to report ``established: true``."""

    max_polls: int
    poll_seconds: float


@dataclass(frozen=True, slots=True)
class RetryConfig:
    """Exponential-with-cap retry around IBKR maintenance windows (ADR 0031 §5)."""

    max_attempts: int
    base_seconds: float
    factor: float
    cap_seconds: float

    def delay_for(self, attempt: int) -> float:
        """Backoff delay before retry ``attempt`` (0-based): ``min(cap, base*factor**a)``."""
        if attempt < 0:
            raise IbkrHistoryConfigError(f"retry attempt must be >= 0, got {attempt}")
        return min(self.cap_seconds, self.base_seconds * self.factor**attempt)


@dataclass(frozen=True, slots=True)
class IbkrHistoryConfig:
    """Resolved, validated IBKR historical-fetch connectivity config."""

    base_url: str
    request_timeout_seconds: float
    max_concurrent_requests: int
    warmup_required: bool
    established_wait: EstablishedWaitConfig
    retry: RetryConfig
    bar: str
    default_period: str
    config_hash: str

    @classmethod
    def from_config(cls, loaded: LoadedConfig) -> IbkrHistoryConfig:
        data = loaded.data
        wait = _require_mapping(data, "established_wait")
        retry = _require_mapping(data, "retry")
        max_concurrent = _require_int(data, "max_concurrent_requests")
        if max_concurrent < 1:
            raise IbkrHistoryConfigError("max_concurrent_requests must be >= 1")
        return cls(
            base_url=_require_str(data, "base_url"),
            request_timeout_seconds=_require_float(data, "request_timeout_seconds"),
            max_concurrent_requests=max_concurrent,
            warmup_required=_require_bool(data, "warmup_required"),
            established_wait=EstablishedWaitConfig(
                max_polls=_require_int(wait, "max_polls"),
                poll_seconds=_require_float(wait, "poll_seconds"),
            ),
            retry=RetryConfig(
                max_attempts=_require_int(retry, "max_attempts"),
                base_seconds=_require_float(retry, "base_seconds"),
                factor=_require_float(retry, "factor"),
                cap_seconds=_require_float(retry, "cap_seconds"),
            ),
            bar=_require_str(data, "bar"),
            default_period=_require_str(data, "default_period"),
            config_hash=loaded.config_hash,
        )


def _require_str(data: object, key: str) -> str:
    value = _require(data, key)
    if not isinstance(value, str) or not value.strip():
        raise IbkrHistoryConfigError(f"{key!r} must be a non-empty string, got {value!r}")
    return value


def _require_float(data: object, key: str) -> float:
    value = _require(data, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IbkrHistoryConfigError(f"{key!r} must be a number, got {value!r}")
    return float(value)


def _require_int(data: object, key: str) -> int:
    value = _require(data, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise IbkrHistoryConfigError(f"{key!r} must be an integer, got {value!r}")
    return value


def _require_bool(data: object, key: str) -> bool:
    value = _require(data, key)
    if not isinstance(value, bool):
        raise IbkrHistoryConfigError(f"{key!r} must be a boolean, got {value!r}")
    return value


def _require_mapping(data: object, key: str) -> object:
    value = _require(data, key)
    if not hasattr(value, "get") or not hasattr(value, "__getitem__"):
        raise IbkrHistoryConfigError(f"{key!r} must be a mapping, got {value!r}")
    return value


def _require(data: object, key: str) -> object:
    if not hasattr(data, "get"):
        raise IbkrHistoryConfigError(f"expected a mapping to read {key!r} from, got {data!r}")
    value = data.get(key)
    if value is None:
        raise IbkrHistoryConfigError(f"missing required field {key!r}")
    return value


def load_ibkr_history_config(path: str | Path | None = None) -> IbkrHistoryConfig:
    """Load and validate the IBKR history config from ``configs/ibkr_history.yaml``."""
    return IbkrHistoryConfig.from_config(
        load_yaml_config(Path(path) if path else _DEFAULT_CONFIG_PATH)
    )
