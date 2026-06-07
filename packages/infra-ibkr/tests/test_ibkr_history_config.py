"""IBKR history config loads, validates, and rejects malformed values (ADR 0031 / C7).

The connectivity knobs (base_url, timeouts, the 5-concurrent cap, established-wait, retry) come
from validated config, not .py literals (the C7 no-hardcode discipline). These tests pin that the
committed ``configs/ibkr_history.yaml`` loads into the typed object and that a malformed field is a
labeled error, never a silent default.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from algotrading.core.config import load_yaml_config
from algotrading.infra_ibkr.config import (
    IbkrHistoryConfig,
    IbkrHistoryConfigError,
    load_ibkr_history_config,
)


def test_committed_config_loads_with_expected_shape() -> None:
    cfg = load_ibkr_history_config()
    assert cfg.base_url.startswith("https://")
    assert cfg.max_concurrent_requests == 5  # ADR 0031 §5 cap
    assert cfg.warmup_required is True
    assert cfg.bar == "1d"  # /iserver/marketdata/history, never the deprecated /hmds
    assert cfg.config_hash  # carries a provenance hash
    assert cfg.established_wait.max_polls > 0
    assert cfg.retry.max_attempts > 0


def test_retry_delay_is_exponential_with_cap() -> None:
    cfg = load_ibkr_history_config()
    r = cfg.retry
    # Independent oracle: min(cap, base*factor**a), recomputed here from the config fields.
    for attempt in range(6):
        expected = min(r.cap_seconds, r.base_seconds * r.factor**attempt)
        assert r.delay_for(attempt) == pytest.approx(expected)


def test_missing_required_field_is_a_labeled_error(tmp_path: Path) -> None:
    bad = tmp_path / "ibkr_history.yaml"
    bad.write_text("version: '1'\nbase_url: 'https://x'\n")  # missing most required fields
    with pytest.raises(IbkrHistoryConfigError, match="missing required field"):
        load_ibkr_history_config(bad)


def test_zero_concurrency_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "ibkr_history.yaml"
    bad.write_text(
        "version: '1'\n"
        "base_url: 'https://x'\n"
        "request_timeout_seconds: 1.0\n"
        "max_concurrent_requests: 0\n"
        "warmup_required: true\n"
        "established_wait: {max_polls: 1, poll_seconds: 1.0}\n"
        "retry: {max_attempts: 1, base_seconds: 1.0, factor: 2.0, cap_seconds: 1.0}\n"
        "bar: '1d'\n"
        "default_period: '1y'\n"
    )
    with pytest.raises(IbkrHistoryConfigError, match="max_concurrent_requests must be >= 1"):
        load_ibkr_history_config(bad)


def test_non_numeric_timeout_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "ibkr_history.yaml"
    bad.write_text(
        "version: '1'\n"
        "base_url: 'https://x'\n"
        "request_timeout_seconds: 'soon'\n"
        "max_concurrent_requests: 5\n"
        "warmup_required: true\n"
        "established_wait: {max_polls: 1, poll_seconds: 1.0}\n"
        "retry: {max_attempts: 1, base_seconds: 1.0, factor: 2.0, cap_seconds: 1.0}\n"
        "bar: '1d'\n"
        "default_period: '1y'\n"
    )
    with pytest.raises(IbkrHistoryConfigError, match="must be a number"):
        load_ibkr_history_config(bad)


def test_config_hash_round_trips_from_loaded_config() -> None:
    loaded = load_yaml_config(
        Path(__file__).resolve().parents[1] / "configs" / "ibkr_history.yaml"
    )
    cfg = IbkrHistoryConfig.from_config(loaded)
    assert cfg.config_hash == loaded.config_hash
