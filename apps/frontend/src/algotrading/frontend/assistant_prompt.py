from __future__ import annotations

import re

from .grounding import MODE_INDICATIVE, GroundingContext
from .openrouter import ChatMessage

_HONEST_GAP = (
    "Ça n'est pas dans ce que l'écran affiche pour cette clôture — "
    "je ne vais pas l'inventer."
)

_SYSTEM_RULES = (
    "Tu es l'assistant du cockpit de volatilité, pour un gérant de portefeuille (PM). "
    "Tu expliques ce que l'écran affiche, en français clair, en registre PM "
    "(« cotations », « deux-faces », « exclues », « clôture » — jamais le jargon moteur). "
    "RÈGLE ABSOLUE : tu ne calcules, n'interpoles, n'estimes JAMAIS aucune valeur "
    "analytique. Chaque nombre que tu cites doit être repris MOT POUR MOT du bloc de faits "
    "ci-dessous. Si la réponse exige un nombre absent du bloc de faits, réponds exactement : "
    f"« {_HONEST_GAP} » — jamais une valeur plausible inventée. "
    "Tu cites la provenance (sujet · clôture · mode · couverture) quand tu donnes un nombre. "
    "En mode INDICATIF, tu ne présentes jamais une marque indicative comme la clôture stockée."
)

_NUMBER_PATTERN = re.compile(r"-?\d[\d\s,.]*")


def honest_gap_answer() -> str:
    return _HONEST_GAP


def _frame_caption(ctx: GroundingContext) -> str:
    frame = ctx.frame
    parts = [frame.underlying]
    if frame.close_instant is not None:
        parts.append(f"clôture {frame.close_instant}")
    elif frame.trade_date is not None:
        parts.append(frame.trade_date.isoformat())
    parts.append("INDICATIF" if frame.mode == MODE_INDICATIVE else "strict")
    coverage = frame.coverage
    if coverage.option_rows > 0:
        parts.append(f"{coverage.two_sided}/{coverage.option_rows} cotations")
    return " · ".join(parts)


def _facts_block(ctx: GroundingContext) -> str:
    lines = [f"- {fact.label} : {fact.value_text}" for fact in ctx.facts]
    if not lines:
        lines = ["- (aucun fait analytique disponible pour cette clôture)"]
    header = (
        "INDICATIF — marques reconstruites"
        if ctx.frame.mode == MODE_INDICATIVE
        else "strict — clôture stockée"
    )
    intro = "Bloc de faits (les seules valeurs que tu peux citer) :"
    return f"Mode : {header}\n{intro}\n" + "\n".join(lines)


def build_messages(ctx: GroundingContext, question: str) -> list[ChatMessage]:
    user = (
        f"Frame : {_frame_caption(ctx)}\n\n"
        f"{_facts_block(ctx)}\n\n"
        f"Question du PM : {question}"
    )
    return [
        ChatMessage(role="system", content=_SYSTEM_RULES),
        ChatMessage(role="user", content=user),
    ]


def _normalize_number(token: str) -> str | None:
    cleaned = token.strip().rstrip(".").replace(" ", "").replace(",", "")
    if cleaned in ("", "-", "."):
        return None
    try:
        return repr(float(cleaned))
    except ValueError:
        return None


def _allowed_numbers(ctx: GroundingContext) -> set[str]:
    allowed: set[str] = set()
    for fact in ctx.facts:
        for token in _NUMBER_PATTERN.findall(fact.value_text):
            normalized = _normalize_number(token)
            if normalized is not None:
                allowed.add(normalized)
        if fact.raw_value is not None:
            allowed.add(repr(float(fact.raw_value)))
    frame = ctx.frame
    coverage = frame.coverage
    for value in (coverage.option_rows, coverage.two_sided, coverage.excluded):
        allowed.add(repr(float(value)))
    return allowed


def ungrounded_numbers(answer: str, ctx: GroundingContext) -> list[str]:
    allowed = _allowed_numbers(ctx)
    offending: list[str] = []
    for token in _NUMBER_PATTERN.findall(answer):
        normalized = _normalize_number(token)
        if normalized is None:
            continue
        if normalized not in allowed:
            offending.append(token.strip())
    return offending


def is_grounded(answer: str, ctx: GroundingContext) -> bool:
    return not ungrounded_numbers(answer, ctx)
