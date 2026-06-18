from __future__ import annotations

from pathlib import Path

from algotrading.frontend.envfile import load_dotenv, parse_env_text


def test_parse_skips_blanks_comments_and_malformed() -> None:
    text = "\n".join(
        [
            "# a comment",
            "",
            "   ",
            "OPENROUTER_API_KEY=sk-or-secret",
            "no_equals_here",
            "ASSISTANT_MODEL=qwen/qwen3.6-flash",
        ]
    )
    parsed = parse_env_text(text)
    assert parsed == {
        "OPENROUTER_API_KEY": "sk-or-secret",
        "ASSISTANT_MODEL": "qwen/qwen3.6-flash",
    }


def test_parse_strips_matching_quotes_only() -> None:
    parsed = parse_env_text(
        "\n".join(
            [
                "SINGLE='mAAtthieu99*ripplexd'",
                'DOUBLE="hello world"',
                "BARE=plain=value=with=equals",
                "MISMATCHED='oops\"",
            ]
        )
    )
    assert parsed["SINGLE"] == "mAAtthieu99*ripplexd"
    assert parsed["DOUBLE"] == "hello world"
    # partition keeps everything after the first '=' verbatim
    assert parsed["BARE"] == "plain=value=with=equals"
    # only a matching leading/trailing quote pair is stripped
    assert parsed["MISMATCHED"] == "'oops\""


def test_load_does_not_override_real_environment(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ASSISTANT_MODEL=from-file\nNEW_ONLY=from-file\n")
    monkeypatch.setenv("ASSISTANT_MODEL", "from-shell")
    monkeypatch.delenv("NEW_ONLY", raising=False)

    load_dotenv(env_file)

    import os

    # the real shell value wins; the file only fills in what's missing
    assert os.environ["ASSISTANT_MODEL"] == "from-shell"
    assert os.environ["NEW_ONLY"] == "from-file"


def test_load_override_lets_file_win(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("ASSISTANT_MODEL=from-file\n")
    monkeypatch.setenv("ASSISTANT_MODEL", "from-shell")

    load_dotenv(env_file, override=True)

    import os

    assert os.environ["ASSISTANT_MODEL"] == "from-file"


def test_load_missing_file_is_noop(tmp_path: Path) -> None:
    # no exception, no change
    load_dotenv(tmp_path / "does-not-exist.env")
