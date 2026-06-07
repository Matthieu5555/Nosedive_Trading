"""The entrypoint ``.env`` loader (``infra/connectivity/dotenv.py``).

The credential boundary is ``os.environ``; nothing auto-loads ``.env``, so the EOD/backfill
entrypoints load it themselves. These tests pin the parser's contract against an injected target
mapping (never the real ``os.environ``), so they neither read nor mutate the process environment.
"""

from __future__ import annotations

from pathlib import Path

from algotrading.infra.connectivity import load_env_file


def _write(tmp_path: Path, body: str) -> Path:
    env = tmp_path / ".env"
    env.write_text(body, encoding="utf-8")
    return env


def test_loads_key_values_into_target(tmp_path: Path) -> None:
    target: dict[str, str] = {}
    n = load_env_file(
        _write(tmp_path, "IBKR_CP_CONSUMER_KEY=abc123\nIBKR_CP_DH_PRIME=FF00\n"),
        environ=target,
    )
    assert n == 2
    assert target == {"IBKR_CP_CONSUMER_KEY": "abc123", "IBKR_CP_DH_PRIME": "FF00"}


def test_skips_comments_blanks_and_malformed(tmp_path: Path) -> None:
    body = "# a comment\n\n   \nNOEQUALSIGN\nKEY=value\n# trailing\n"
    target: dict[str, str] = {}
    assert load_env_file(_write(tmp_path, body), environ=target) == 1
    assert target == {"KEY": "value"}


def test_strips_export_prefix_and_unwraps_quotes(tmp_path: Path) -> None:
    body = 'export IBKR_CP_ACCESS_TOKEN="tok en"\nIBKR_CP_REALM=\'limited_poa\'\n'
    target: dict[str, str] = {}
    load_env_file(_write(tmp_path, body), environ=target)
    assert target["IBKR_CP_ACCESS_TOKEN"] == "tok en"
    assert target["IBKR_CP_REALM"] == "limited_poa"


def test_real_environment_wins_unless_override(tmp_path: Path) -> None:
    env = _write(tmp_path, "KEY=from_file\n")
    target = {"KEY": "from_env"}
    # default: a key already set is left untouched (the real env outranks the file)
    assert load_env_file(env, environ=target) == 0
    assert target["KEY"] == "from_env"
    # override=True: the file wins
    assert load_env_file(env, environ=target, override=True) == 1
    assert target["KEY"] == "from_file"


def test_blank_value_loads_as_empty_string(tmp_path: Path) -> None:
    target: dict[str, str] = {}
    load_env_file(_write(tmp_path, "IBKR_CP_DH_GENERATOR=\n"), environ=target)
    assert target["IBKR_CP_DH_GENERATOR"] == ""


def test_missing_file_is_a_clean_noop(tmp_path: Path) -> None:
    target: dict[str, str] = {}
    assert load_env_file(tmp_path / "nope.env", environ=target) == 0
    assert target == {}
