from __future__ import annotations

from pathlib import Path

import pytest
from algotrading.infra_ibkr.connectivity.cp_rest_credentials import (
    credentials_present,
    load_lst_consumer,
)
from algotrading.infra_ibkr.connectivity.cp_rest_oauth import CpOAuthError

_DH_PRIME_HEX = "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E08"


def _write_pem(path: Path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


def _full_env(tmp_path: Path) -> dict[str, str]:
    return {
        "IBKR_CP_CONSUMER_KEY": "TESTCONSUMER",
        "IBKR_CP_ACCESS_TOKEN": "ACCESSTOKEN",
        "IBKR_CP_ACCESS_TOKEN_SECRET": "c2VjcmV0",
        "IBKR_CP_SIGNING_KEY_PEM": _write_pem(tmp_path / "sign.pem", "-----SIGNING KEY-----"),
        "IBKR_CP_ENCRYPTION_KEY_PEM": _write_pem(tmp_path / "enc.pem", "-----ENC KEY-----"),
        "IBKR_CP_DH_PRIME": _DH_PRIME_HEX,
    }


def test_blank_environment_is_not_credentialed_and_loads_none() -> None:
    assert credentials_present({}) is False
    assert load_lst_consumer({}) is None


def test_full_environment_is_credentialed_and_assembles_the_consumer(tmp_path: Path) -> None:
    env = _full_env(tmp_path)
    assert credentials_present(env) is True

    consumer = load_lst_consumer(env)
    assert consumer is not None
    assert consumer.consumer_key == "TESTCONSUMER"
    assert consumer.access_token == "ACCESSTOKEN"
    assert consumer.access_token_secret == "c2VjcmV0"
    assert consumer.signing_key_pem == "-----SIGNING KEY-----"
    assert consumer.encryption_key_pem == "-----ENC KEY-----"
    assert consumer.dh.prime == int(_DH_PRIME_HEX, 16)
    assert consumer.dh.generator == 2
    assert consumer.realm == "limited_poa"


def test_optional_generator_and_realm_overrides_are_honored(tmp_path: Path) -> None:
    env = _full_env(tmp_path) | {"IBKR_CP_DH_GENERATOR": "5", "IBKR_CP_REALM": "test_poa"}
    consumer = load_lst_consumer(env)
    assert consumer is not None
    assert consumer.dh.generator == 5
    assert consumer.realm == "test_poa"


def test_partial_environment_is_a_labeled_error_not_silent(tmp_path: Path) -> None:
    env = _full_env(tmp_path)
    del env["IBKR_CP_ACCESS_TOKEN"]
    assert credentials_present(env) is False
    with pytest.raises(CpOAuthError) as exc:
        load_lst_consumer(env)
    assert "IBKR_CP_ACCESS_TOKEN" in str(exc.value)


def test_whitespace_only_value_counts_as_absent(tmp_path: Path) -> None:
    env = _full_env(tmp_path)
    env["IBKR_CP_CONSUMER_KEY"] = "   "
    assert credentials_present(env) is False
    with pytest.raises(CpOAuthError):
        load_lst_consumer(env)


def test_missing_pem_file_is_a_labeled_error(tmp_path: Path) -> None:
    env = _full_env(tmp_path)
    env["IBKR_CP_SIGNING_KEY_PEM"] = str(tmp_path / "does-not-exist.pem")
    with pytest.raises(CpOAuthError) as exc:
        load_lst_consumer(env)
    assert "IBKR_CP_SIGNING_KEY_PEM" in str(exc.value)
