"""Tests for Saxo environment config loading and resolution."""

from __future__ import annotations

import pytest
from algotrading.infra_saxo.config import (
    SaxoConfig,
    base_url_for,
    load_saxo_config,
    resolve_environment,
)
from algotrading.infra_saxo.connectivity.saxo_transport import SaxoTransport


def _write_yaml(tmp_path, environment: str = "sim", delay: int = 15):
    p = tmp_path / "saxo.yaml"
    p.write_text(f"environment: {environment}\nexpected_delay_minutes: {delay}\n")
    return p


class TestLoadSaxoConfig:
    def test_loads_default_packaged_config(self):
        cfg = load_saxo_config()
        assert cfg.environment in ("sim", "live")
        assert cfg.expected_delay_minutes == 15
        assert cfg.config_hash  # provenance present

    def test_loads_explicit_path(self, tmp_path):
        cfg = load_saxo_config(_write_yaml(tmp_path, "live", 20))
        assert cfg.environment == "live"
        assert cfg.expected_delay_minutes == 20

    def test_invalid_environment_in_yaml_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Invalid Saxo environment"):
            load_saxo_config(_write_yaml(tmp_path, "paper"))


class TestResolveEnvironment:
    def test_yaml_default_when_no_override(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SAXO_ENV", raising=False)
        cfg = SaxoConfig(environment="sim", expected_delay_minutes=15, config_hash="x")
        assert resolve_environment(config=cfg) == "sim"

    def test_env_var_overrides_yaml(self, monkeypatch):
        monkeypatch.setenv("SAXO_ENV", "live")
        cfg = SaxoConfig(environment="sim", expected_delay_minutes=15, config_hash="x")
        assert resolve_environment(config=cfg) == "live"

    def test_cli_overrides_env_var(self, monkeypatch):
        monkeypatch.setenv("SAXO_ENV", "sim")
        assert resolve_environment(cli_env="live") == "live"

    def test_invalid_value_raises(self, monkeypatch):
        monkeypatch.delenv("SAXO_ENV", raising=False)
        with pytest.raises(ValueError, match="Invalid Saxo environment"):
            resolve_environment(cli_env="demo")


class TestBaseUrl:
    def test_live_maps_to_live_gateway(self):
        assert base_url_for("live") == SaxoTransport.LIVE_BASE_URL

    def test_sim_maps_to_sim_gateway(self):
        assert base_url_for("sim") == SaxoTransport.SIM_BASE_URL
