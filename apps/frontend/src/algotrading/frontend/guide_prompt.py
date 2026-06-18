from __future__ import annotations

import json
from dataclasses import dataclass

from .openrouter import ChatMessage

# The three actions the front's guide loop knows how to advance on (assistantApi.ts GuideExpect).
EXPECT_VALUES = ("navigate", "click", "none")
_DEFAULT_EXPECT = "none"

# A short, honest fallback used when the model gives us no usable instruction. No em dashes.
_FALLBACK_SAY = "I'm not sure of the next step, try a different question."

_SYSTEM_RULES = (
    "You are the cockpit guide. You walk a portfolio manager (PM) through the UI, "
    "one short step at a time, in plain English with no jargon. "
    "Each reply is a SINGLE next step, never a list. "
    "Keep the instruction short (about 14 words or fewer) and concrete, "
    "for example \"Click Basket up top.\" or \"Pick an index in the picker.\". "
    "Never use em dashes; use commas. "
    "You may only point at on-screen elements from the catalog below. "
    "ABSOLUTE RULE: the highlight value must be one of the catalog ids exactly, or null. "
    "You NEVER invent an id, and you NEVER reference an element that is not in the catalog. "
    "Consider where the user already is (route) and what they have already done (completed) "
    "so you emit the NEXT step, not a step they have finished or are already on. "
    "Set expect to \"navigate\" when the next action opens a different page "
    "(highlight the matching nav.* id), \"click\" when the action is clicking the highlighted "
    "element on the current page, and \"none\" for an informational or final step. "
    "Set done to true only when the goal has been reached. "
    "Return ONLY a single JSON object, no prose and no code fences, of the exact shape: "
    "{\"say\": string, \"highlight\": string or null, "
    "\"expect\": \"navigate\" | \"click\" | \"none\", \"done\": boolean}."
)


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """One highlightable anchor as posted by the front's tourCatalog()."""

    id: str
    label: str
    description: str
    route: str


def _catalog_block(catalog: list[CatalogEntry]) -> str:
    if not catalog:
        return "Catalog (the only elements you may highlight):\n- (no elements available)"
    lines = [
        f"- {entry.id} · {entry.label} · {entry.description} · route {entry.route}"
        for entry in catalog
    ]
    intro = "Catalog (the only elements you may highlight, by id):"
    return intro + "\n" + "\n".join(lines)


def build_guide_messages(
    goal: str,
    route: str,
    completed: list[str],
    catalog: list[CatalogEntry],
) -> list[ChatMessage]:
    completed_text = ", ".join(completed) if completed else "(none yet)"
    user = (
        f"Goal: {goal}\n"
        f"Current route: {route}\n"
        f"Steps already completed: {completed_text}\n\n"
        f"{_catalog_block(catalog)}\n\n"
        "Emit the next single step as the JSON object."
    )
    return [
        ChatMessage(role="system", content=_SYSTEM_RULES),
        ChatMessage(role="user", content=user),
    ]


def _extract_json_object(raw: str) -> dict | None:
    """Pull the first balanced {...} object out of raw model text.

    Tolerates code fences and surrounding prose. Returns the decoded dict, or None
    if nothing parseable is found.
    """
    # Try a direct parse first (the well-behaved case).
    stripped = raw.strip()
    try:
        decoded = json.loads(stripped)
    except ValueError:
        decoded = None
    if isinstance(decoded, dict):
        return decoded

    # Scan for the first balanced brace span and try to decode each candidate.
    depth = 0
    start = -1
    for index, char in enumerate(raw):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = raw[start : index + 1]
                    try:
                        decoded = json.loads(candidate)
                    except ValueError:
                        decoded = None
                    if isinstance(decoded, dict):
                        return decoded
                    start = -1
    return None


def _coerce_say(value: object) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return _FALLBACK_SAY


def _coerce_highlight(value: object, catalog_ids: set[str]) -> str | None:
    # The navigation analogue of nulling an ungrounded number: any id not in the
    # posted catalog is dropped to null, never trusted.
    if isinstance(value, str) and value in catalog_ids:
        return value
    return None


def _coerce_expect(value: object) -> str:
    if isinstance(value, str) and value in EXPECT_VALUES:
        return value
    return _DEFAULT_EXPECT


def _coerce_done(value: object) -> bool:
    return bool(value)


def parse_guide_step(raw: str, catalog_ids: set[str]) -> dict:
    """Parse and validate a model guide step into a clean dict.

    Pure function, no network. This is the trust guard: it tolerates code fences and
    surrounding prose, then validates highlight against the posted catalog (nulling any
    invented id), coerces expect to a known value, done to a bool, and guarantees a
    non-empty say.
    """
    parsed = _extract_json_object(raw) or {}
    return {
        "say": _coerce_say(parsed.get("say")),
        "highlight": _coerce_highlight(parsed.get("highlight"), catalog_ids),
        "expect": _coerce_expect(parsed.get("expect")),
        "done": _coerce_done(parsed.get("done")),
    }
