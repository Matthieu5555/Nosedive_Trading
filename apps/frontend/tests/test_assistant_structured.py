"""Unit tests for the grounded-by-construction structured answer.

These exercise the pure logic (pydantic validator + placeholder rendering + prompt) with no
network: the validator's allowed-id set is the same one the BFF threads in through instructor's
validation context.
"""

from __future__ import annotations

import pytest
from algotrading.frontend.assistant_structured import (
    GroundedAnswer,
    allowed_fact_ids,
    build_grounded_messages,
    render_grounded,
)
from algotrading.frontend.grounding import (
    Coverage,
    Fact,
    Frame,
    GroundingContext,
)

ALLOWED = {"atm_level", "surface_coverage", "excluded_rows"}


def _ctx() -> GroundingContext:
    coverage = Coverage(option_rows=6, two_sided=4, excluded=2, two_sided_fraction=4 / 6)
    frame = Frame(
        underlying="SX5E",
        trade_date=None,
        close_instant="17:30 CEST",
        mode="strict",
        coverage=coverage,
    )
    facts = [
        Fact("atm_level", "ATM (at-the-money implied vol)", "1.84 × 10⁻¹ Vol", 0.184, "Vol"),
        Fact("surface_coverage", "Surface coverage", "4/6 two-sided quotes", 4 / 6, None),
        Fact("excluded_rows", "Excluded quotes", "2 excluded", 2.0, None),
    ]
    return GroundingContext(frame=frame, facts=facts, tenor_label="3m")


def _validate(answer: str, *, answerable: bool = True) -> GroundedAnswer:
    return GroundedAnswer.model_validate(
        {"answerable": answerable, "answer": answer, "facts_used": []},
        context={"allowed_ids": ALLOWED},
    )


# --- Validator: the heart of the guarantee -------------------------------------------------

def test_validator_accepts_known_placeholders() -> None:
    parsed = _validate("The ATM sits at {atm_level} with {surface_coverage} on screen.")
    assert "{atm_level}" in parsed.answer


def test_validator_rejects_an_unknown_placeholder() -> None:
    with pytest.raises(ValueError, match="Unknown fact id"):
        _validate("The vol-of-vol is {vol_of_vol}.")


def test_validator_rejects_a_bare_digit() -> None:
    # A literal number outside a placeholder is exactly the fabrication failure mode.
    with pytest.raises(ValueError, match="Do not write digits"):
        _validate("The ATM is at 30%.")


def test_validator_accepts_prose_with_no_numbers_when_unanswerable() -> None:
    parsed = _validate(
        "That figure is not on this screen, choose a tenor that carries it.",
        answerable=False,
    )
    assert parsed.answerable is False


def test_validator_skips_without_context() -> None:
    # Plain construction (no validation context) cannot know the vocabulary, so it does not
    # guess: a fabricated string is allowed through, which is how the test fakes wrap one.
    parsed = GroundedAnswer(answerable=True, answer="The ATM is at 30%.", facts_used=[])
    assert "30%" in parsed.answer


# --- Rendering -----------------------------------------------------------------------------

def test_render_substitutes_placeholders_with_exact_value_text() -> None:
    ctx = _ctx()
    parsed = GroundedAnswer(
        answerable=True,
        answer="The ATM is {atm_level}, on {surface_coverage}.",
        facts_used=["atm_level", "surface_coverage"],
    )
    rendered = render_grounded(parsed, ctx)
    assert rendered == "The ATM is 1.84 × 10⁻¹ Vol, on 4/6 two-sided quotes."
    # Crucially: every digit in the rendered text traces to a fact's value_text, never the model.
    assert "{" not in rendered


def test_render_leaves_an_unknown_placeholder_verbatim() -> None:
    ctx = _ctx()
    parsed = GroundedAnswer(answerable=True, answer="x {nope} y", facts_used=[])
    assert render_grounded(parsed, ctx) == "x {nope} y"


# --- Vocabulary + prompt -------------------------------------------------------------------

def test_allowed_fact_ids_matches_the_facts() -> None:
    assert allowed_fact_ids(_ctx()) == ALLOWED


def test_prompt_lists_every_fact_id_as_a_placeholder() -> None:
    messages = build_grounded_messages(_ctx(), "What is the ATM?")
    user = messages[1].content
    for fact_id in ALLOWED:
        assert f"{{{fact_id}}}" in user
    assert "What is the ATM?" in user
    assert messages[0].role == "system"
