"""upsert_env_vars: set keys in a .env body without disturbing unrelated lines."""

from algotrading.infra_saxo.auth import upsert_env_vars


def test_replaces_existing_key_in_place() -> None:
    text = "A=1\nSAXO_ACCESS_TOKEN=old\nB=2\n"
    assert (
        upsert_env_vars(text, {"SAXO_ACCESS_TOKEN": "new"}) == "A=1\nSAXO_ACCESS_TOKEN=new\nB=2\n"
    )


def test_appends_missing_key() -> None:
    assert upsert_env_vars("A=1\n", {"SAXO_REFRESH_TOKEN": "r"}) == "A=1\nSAXO_REFRESH_TOKEN=r\n"


def test_preserves_comments_and_blank_lines() -> None:
    assert upsert_env_vars("# c\n\nA=1\n", {"A": "2"}) == "# c\n\nA=2\n"


def test_commented_assignment_is_not_treated_as_a_key() -> None:
    # The comment stays; the key is appended as new rather than overwriting the comment.
    assert upsert_env_vars("# A=1\n", {"A": "2"}) == "# A=1\nA=2\n"


def test_rotates_both_tokens_together() -> None:
    text = "SAXO_ACCESS_TOKEN=a0\nSAXO_REFRESH_TOKEN=r0\n"
    out = upsert_env_vars(text, {"SAXO_ACCESS_TOKEN": "a1", "SAXO_REFRESH_TOKEN": "r1"})
    assert out == "SAXO_ACCESS_TOKEN=a1\nSAXO_REFRESH_TOKEN=r1\n"
