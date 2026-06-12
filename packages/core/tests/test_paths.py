"""``algotrading.core.paths`` — the one workspace-anchor + ``.env``-loading seam (audit M23).

Expected values are derived from the workspace layout (AGENTS.md: the root holds
``pyproject.toml`` / ``AGENTS.md`` / ``packages/``) and from the documented precedence
contract (real environment wins over the file), not from the code under test.
"""

from __future__ import annotations

import os
from pathlib import Path

import algotrading.core.paths as paths
import pytest
from algotrading.core.paths import DATA_ROOT_ENV_VAR, data_root, load_env_file, repo_root


def test_repo_root_is_the_workspace_root() -> None:
    # Independently derived: the workspace root is the directory carrying the uv
    # workspace pyproject.toml, AGENTS.md, and packages/ (AGENTS.md "Orient yourself").
    root = repo_root()
    assert (root / "pyproject.toml").is_file()
    assert (root / "AGENTS.md").is_file()
    assert (root / "packages" / "core").is_dir()


def test_data_root_defaults_under_the_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATA_ROOT_ENV_VAR, raising=False)
    assert data_root() == repo_root() / "data"


def test_data_root_honors_the_env_var(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(DATA_ROOT_ENV_VAR, str(tmp_path / "store"))
    assert data_root() == tmp_path / "store"


def test_data_root_treats_empty_env_var_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_ROOT_ENV_VAR, "")
    assert data_root() == repo_root() / "data"


def test_load_env_file_sets_new_variables(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = tmp_path / ".env"
    env.write_text('ALGO_TEST_A=plain\nALGO_TEST_B="quoted value"\n# a comment\n')
    monkeypatch.delenv("ALGO_TEST_A", raising=False)
    monkeypatch.delenv("ALGO_TEST_B", raising=False)
    assert load_env_file(env) is True
    assert os.environ["ALGO_TEST_A"] == "plain"
    assert os.environ["ALGO_TEST_B"] == "quoted value"  # matched quotes unwrapped


def test_real_environment_wins_over_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The documented precedence: a systemd EnvironmentFile / shell export is never
    # shadowed by a stale .env (override=False).
    env = tmp_path / ".env"
    env.write_text("ALGO_TEST_PRECEDENCE=file\n")
    monkeypatch.setenv("ALGO_TEST_PRECEDENCE", "shell")
    load_env_file(env)
    assert os.environ["ALGO_TEST_PRECEDENCE"] == "shell"


def test_missing_file_is_a_clean_no_op(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "nope.env") is False


def test_values_load_literally_without_interpolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # interpolate=False: a credential containing ``${`` must load byte-for-byte.
    env = tmp_path / ".env"
    env.write_text("ALGO_TEST_LITERAL=pa${HOME}ss\n")
    monkeypatch.delenv("ALGO_TEST_LITERAL", raising=False)
    load_env_file(env)
    assert os.environ["ALGO_TEST_LITERAL"] == "pa${HOME}ss"


def test_default_path_is_the_repo_root_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # With no argument the loader reads <repo>/.env; anchor the module at a temp "repo"
    # so the test never touches the real (credentialed, gitignored) file.
    monkeypatch.setattr(paths, "_REPO_ROOT", tmp_path)
    (tmp_path / ".env").write_text("ALGO_TEST_DEFAULT_PATH=from-default\n")
    monkeypatch.delenv("ALGO_TEST_DEFAULT_PATH", raising=False)
    assert load_env_file() is True
    assert os.environ["ALGO_TEST_DEFAULT_PATH"] == "from-default"
