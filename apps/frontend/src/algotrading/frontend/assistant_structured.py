"""Grounded-by-construction assistant answers.

The weak/cheap reasoning models we run through OpenRouter (e.g. qwen-flash) are happy
to write fluent prose, but left to free text they sprinkle in plausible-looking numbers
that were never on the screen. The old defence was a post-hoc number guard that nuked the
whole answer to an honest-gap line whenever it caught a fabricated value, so a single
stray digit cost the PM the entire answer.

This module flips the contract: instead of checking numbers after the fact, it never lets
the model emit one. The model returns a small structured object (`GroundedAnswer`) whose
prose carries every number as a ``{fact_id}`` placeholder drawn from the facts block, and
the server renders each placeholder to the fact's exact ``value_text``. A pydantic validator
(fed the allowed ids through the validation context) rejects unknown placeholders and any
bare digit, so when this runs under `instructor` the model is told precisely what it did
wrong and retries, rather than silently failing the whole turn.

The rendered answer can therefore only contain values that are genuinely on the screen.
The legacy `ungrounded_numbers` guard still runs downstream as a cheap belt-and-suspenders
net, but in the structured path it should never fire.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from .grounding import MODE_INDICATIVE, GroundingContext
from .openrouter import ChatMessage

# A placeholder is a fact id in braces, e.g. {atm_level}. Fact ids are lower-snake-case
# (grounding.py builds them), so the character class is deliberately narrow.
_PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")
_BARE_DIGIT = re.compile(r"\d")

_ALLOWED_IDS_KEY = "allowed_ids"


class GroundedAnswer(BaseModel):
    """The assistant's answer, grounded by construction.

    `answer` is PM-register prose in which every number is a ``{fact_id}`` placeholder; the
    server fills each one with the fact's exact on-screen text. When the question needs a
    value the facts block does not carry, the model sets ``answerable=False`` and the server
    returns the honest-gap line instead.
    """

    answerable: bool = Field(
        description=(
            "True only when the question can be answered using the listed facts. "
            "False when it needs a value the facts block does not carry."
        )
    )
    answer: str = Field(
        description=(
            "Plain PM-register prose. Reference EVERY number as a {fact_id} placeholder "
            "taken from the facts block, never a literal digit. May be a short empty-ish "
            "sentence when answerable is False."
        )
    )
    facts_used: list[str] = Field(
        default_factory=list,
        description="The fact ids whose placeholders appear in `answer`.",
    )

    @field_validator("answer")
    @classmethod
    def _only_grounded_placeholders(cls, value: str, info: ValidationInfo) -> str:
        # The allowed-id set arrives via instructor's validation_context. With no context
        # (e.g. a plain unit-test construction) we skip the check rather than guess.
        allowed = (info.context or {}).get(_ALLOWED_IDS_KEY)
        if allowed is None:
            return value
        unknown = sorted({ph for ph in _PLACEHOLDER.findall(value) if ph not in allowed})
        if unknown:
            raise ValueError(
                f"Unknown fact id(s) {unknown}. The only placeholders that exist are: "
                f"{sorted(allowed)}. Use one of those, or set answerable=false."
            )
        if _BARE_DIGIT.search(_PLACEHOLDER.sub("", value)):
            raise ValueError(
                "Do not write digits directly. Every number must be a {fact_id} placeholder "
                "drawn from the facts block; the system fills it with the on-screen value."
            )
        return value


def allowed_fact_ids(ctx: GroundingContext) -> set[str]:
    """The placeholder vocabulary the model may use for this close."""
    return {fact.fact_id for fact in ctx.facts}


def render_grounded(parsed: GroundedAnswer, ctx: GroundingContext) -> str:
    """Substitute every ``{fact_id}`` placeholder with the fact's exact ``value_text``.

    An unknown placeholder is left verbatim (the validator already rejects those upstream,
    so this only matters when rendering an unvalidated object in a test).
    """
    values = {fact.fact_id: fact.value_text for fact in ctx.facts}
    return _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), parsed.answer)


_SYSTEM_RULES = (
    "You are the volatility-cockpit assistant, for a portfolio manager (PM). "
    "You explain what the screen shows in plain English, in PM register "
    '("quotes", "two-sided", "excluded", "close"), never engine jargon. '
    "You return a structured answer. ABSOLUTE RULES:\n"
    "1. Every number in `answer` MUST be a {fact_id} placeholder taken from the FACTS list "
    "below. NEVER write a digit yourself; the system replaces each placeholder with the "
    "exact on-screen value.\n"
    "2. Use only the fact ids in the FACTS list. If answering needs a value that is not "
    "listed, set answerable=false and say in one plain sentence (no numbers) what is missing.\n"
    "3. List every fact id you used in `facts_used`.\n"
    "4. Refer to provenance (subject, close, mode, coverage) in words, never as numbers.\n"
    "5. In INDICATIVE mode, never present a reconstructed mark as the stored close."
)


def _frame_caption(ctx: GroundingContext) -> str:
    frame = ctx.frame
    parts = [frame.underlying]
    if frame.close_instant is not None:
        parts.append(f"close {frame.close_instant}")
    elif frame.trade_date is not None:
        parts.append(frame.trade_date.isoformat())
    parts.append("INDICATIVE" if frame.mode == MODE_INDICATIVE else "strict")
    coverage = frame.coverage
    if coverage.option_rows > 0:
        parts.append(f"{coverage.two_sided}/{coverage.option_rows} quotes")
    return " · ".join(parts)


def _facts_listing(ctx: GroundingContext) -> str:
    if not ctx.facts:
        return "FACTS: (no analytics facts available for this close)"
    lines = [
        f"- {{{fact.fact_id}}} = {fact.label} : {fact.value_text}" for fact in ctx.facts
    ]
    header = (
        "INDICATIVE - reconstructed marks"
        if ctx.frame.mode == MODE_INDICATIVE
        else "strict - stored close"
    )
    intro = "FACTS (use these placeholders for every number you write):"
    return f"Mode: {header}\n{intro}\n" + "\n".join(lines)


def build_grounded_messages(ctx: GroundingContext, question: str) -> list[ChatMessage]:
    user = (
        f"Frame: {_frame_caption(ctx)}\n\n"
        f"{_facts_listing(ctx)}\n\n"
        f"PM question: {question}"
    )
    return [
        ChatMessage(role="system", content=_SYSTEM_RULES),
        ChatMessage(role="user", content=user),
    ]
