from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, NoReturn

from algotrading.core.config import LoadedConfig, load_yaml_config
from algotrading.infra.connectivity import BackoffSchedule
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "ibkr_history.yaml"


class IbkrHistoryConfigError(Exception):
    pass


def _raise_history_config_error(exc: ValidationError) -> NoReturn:
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


_NonBlankStr = Annotated[str, AfterValidator(_require_non_blank)]

_HISTORY_MODEL_CONFIG = ConfigDict(frozen=True, extra="ignore", strict=True)


class EstablishedWaitConfig(BaseModel):

    model_config = _HISTORY_MODEL_CONFIG

    max_polls: int
    poll_seconds: float


class RetryConfig(BaseModel):

    model_config = _HISTORY_MODEL_CONFIG

    max_attempts: int
    base_seconds: float
    factor: float
    cap_seconds: float

    def delay_for(self, attempt: int) -> float:
        schedule = BackoffSchedule(
            base_seconds=self.base_seconds, factor=self.factor, cap_seconds=self.cap_seconds
        )
        return schedule.delay_for(attempt)


class IbkrHistoryConfig(BaseModel):

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
    return IbkrHistoryConfig.from_config(
        load_yaml_config(Path(path) if path else _DEFAULT_CONFIG_PATH)
    )
