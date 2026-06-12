"""Tests for the .env rotated-token persister."""

from __future__ import annotations

from pathlib import Path

from algotrading.infra_saxo.auth import make_env_token_persister


def test_persister_upserts_rotated_tokens(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "SAXO_CLIENT_ID=keepme\nSAXO_ACCESS_TOKEN=old_a\nSAXO_REFRESH_TOKEN=old_r\n",
        encoding="utf-8",
    )
    make_env_token_persister(env)("new_a", "new_r")

    body = env.read_text(encoding="utf-8")
    assert "SAXO_ACCESS_TOKEN=new_a" in body
    assert "SAXO_REFRESH_TOKEN=new_r" in body
    assert "SAXO_CLIENT_ID=keepme" in body  # unrelated lines untouched


def test_persister_is_noop_when_env_absent(tmp_path: Path) -> None:
    env = tmp_path / ".env"  # never created
    make_env_token_persister(env)("a", "r")  # must not raise
    assert not env.exists()  # no file conjured into existence (dotenv would create one)


def test_persister_preserves_comments_blank_lines_and_format(tmp_path: Path) -> None:
    """The dotenv upsert must keep the hand-written KEY=value format (no quoting) and leave
    comments and blank lines exactly where they were."""
    env = tmp_path / ".env"
    env.write_text(
        "# saxo credentials\n\nSAXO_CLIENT_ID=keepme\nSAXO_ACCESS_TOKEN=a0\nSAXO_REFRESH_TOKEN=r0\n",
        encoding="utf-8",
    )
    make_env_token_persister(env)("a1", "r1")
    assert env.read_text(encoding="utf-8") == (
        "# saxo credentials\n\nSAXO_CLIENT_ID=keepme\nSAXO_ACCESS_TOKEN=a1\nSAXO_REFRESH_TOKEN=r1\n"
    )


def test_persister_appends_missing_token_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("SAXO_CLIENT_ID=keepme\n", encoding="utf-8")
    make_env_token_persister(env)("a1", "r1")
    body = env.read_text(encoding="utf-8")
    assert "SAXO_CLIENT_ID=keepme" in body
    assert "SAXO_ACCESS_TOKEN=a1" in body
    assert "SAXO_REFRESH_TOKEN=r1" in body
