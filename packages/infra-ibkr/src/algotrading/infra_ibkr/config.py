"""IBKR historical-fetch configuration — typed, validated, no-hardcode (ADR 0031 / C7).

The unattended backfill needs hosts/URLs, timeouts, the 5-concurrent cap, the established-
session wait, and the maintenance-window retry/backoff. None of those are economic (they do
not change *what* is computed), so they are not a hashed bundle — but they are still config,
not ``.py`` literals (the C7 discipline the 1C spec carries forward). The canonical defaults
live in the versioned ``configs/ibkr_history.yaml`` beside the package; this module validates
them into a frozen :class:`IbkrHistoryConfig` through pydantic (the REP6 config seam — strict
types, no lossy coercion), re-raising any rejection as a labeled :class:`IbkrHistoryConfigError`.

Secrets never pass through here: the OAuth consumer key/secret and the Live Session Token are
read from ``.env`` by the caller and handed to the signer; this object carries only the
non-secret connectivity knobs.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, NoReturn

from algotrading.core.config import LoadedConfig, load_yaml_config
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

# configs/ibkr_history.yaml sits beside src/: src/algotrading/infra_ibkr/config.py
# parents[3] == packages/infra-ibkr
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "ibkr_history.yaml"


class IbkrHistoryConfigError(Exception):
    """A field in the IBKR history config is missing or malformed — labeled, never silent."""


def _raise_history_config_error(exc: ValidationError) -> NoReturn:
    """Map a pydantic rejection onto the labeled :class:`IbkrHistoryConfigError`.

    Takes the first reported error and names the offending field (dotted for a nested one,
    e.g. ``retry.factor``), preserving the "missing required field" wording and carrying
    pydantic's reason for everything else — a bad config names exactly what was wrong.
    """
    error = exc.errors()[0]
    location = error.get("loc", ())
    field = ".".join(str(part) for part in location) if location else "<root>"
    if error.get("type") == "missing":
        raise IbkrHistoryConfigError(f"missing required field {field!r}") from exc
    raise IbkrHistoryConfigError(
        f"{field!r}: {error.get('msg', '')} (got {error.get('input')!r})"
    ) from exc


def _require_non_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must be a non-empty string")
    return value


# A required string that is not empty/whitespace (URLs, bar size, period).
_NonBlankStr = Annotated[str, AfterValidator(_require_non_blank)]

# Frozen (immutable, hashable), unknown YAML keys ignored (the file carries e.g. `version`),
# and strict scalar typing — no lossy coercion (`"5"`/`5.5` → int and bool → number rejected;
# an int is still a valid float, as YAML writes `15` for `15.0`).
_HISTORY_MODEL_CONFIG = ConfigDict(frozen=True, extra="ignore", strict=True)


class EstablishedWaitConfig(BaseModel):
    """How long to wait for the brokerage session to report ``established: true``."""

    model_config = _HISTORY_MODEL_CONFIG

    max_polls: int
    poll_seconds: float


class RetryConfig(BaseModel):
    """Exponential-with-cap retry around IBKR maintenance windows (ADR 0031 §5)."""

    model_config = _HISTORY_MODEL_CONFIG

    max_attempts: int
    base_seconds: float
    factor: float
    cap_seconds: float

    def delay_for(self, attempt: int) -> float:
        """Backoff delay before retry ``attempt`` (0-based): ``min(cap, base*factor**a)``."""
        if attempt < 0:
            raise IbkrHistoryConfigError(f"retry attempt must be >= 0, got {attempt}")
        return min(self.cap_seconds, self.base_seconds * self.factor**attempt)


class IbkrHistoryConfig(BaseModel):
    """Resolved, validated IBKR historical-fetch connectivity config."""

    model_config = _HISTORY_MODEL_CONFIG

    base_url: _NonBlankStr
    request_timeout_seconds: float
    max_concurrent_requests: int = Field(ge=1)
    warmup_required: bool
    established_wait: EstablishedWaitConfig
    retry: RetryConfig
    bar: _NonBlankStr
    default_period: _NonBlankStr
    config_hash: str

    @classmethod
    def from_config(cls, loaded: LoadedConfig) -> IbkrHistoryConfig:
        # LoadedConfig freezes its sections as mapping proxies; strict validation wants real
        # dicts for the nested models, so thaw one level (the file is flat-plus-two-blocks).
        data: dict[str, object] = {
            key: dict(value) if isinstance(value, Mapping) else value
            for key, value in loaded.data.items()
        }
        data["config_hash"] = loaded.config_hash
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            _raise_history_config_error(exc)


def load_ibkr_history_config(path: str | Path | None = None) -> IbkrHistoryConfig:
    """Load and validate the IBKR history config from ``configs/ibkr_history.yaml``."""
    return IbkrHistoryConfig.from_config(
        load_yaml_config(Path(path) if path else _DEFAULT_CONFIG_PATH)
    )
