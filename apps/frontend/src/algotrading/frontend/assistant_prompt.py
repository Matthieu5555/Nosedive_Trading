from __future__ import annotations

import re
import unicodedata

from .grounding import MODE_INDICATIVE, GroundingContext
from .openrouter import ChatMessage

_HONEST_GAP = (
    "That isn't in what the screen shows for this close — "
    "I won't make it up."
)

_SYSTEM_RULES = (
    "You are the volatility-cockpit assistant, for a portfolio manager (PM). "
    "You explain what the screen shows, in plain English, in PM register "
    "(\"quotes\", \"two-sided\", \"excluded\", \"close\" — never the engine jargon). "
    "ABSOLUTE RULE: you NEVER compute, interpolate, or estimate any analytics "
    "value. Every number you cite must be taken WORD FOR WORD from the facts block "
    "below — digits, scientific notation (× 10ⁿ), or a number spelled out in words "
    "(\"thirty percent\" counts as a number). If the answer requires a number absent from "
    "the facts block, reply exactly: "
    f"\"{_HONEST_GAP}\" — never an invented plausible value. "
    "You cite the provenance (subject · close · mode · coverage) when you give a number. "
    "In INDICATIVE mode, you never present an indicative mark as the stored close."
)

_VALUE_TOLERANCE = 1e-9


def honest_gap_answer() -> str:
    return _HONEST_GAP


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


def _facts_block(ctx: GroundingContext) -> str:
    lines = [f"- {fact.label} : {fact.value_text}" for fact in ctx.facts]
    if not lines:
        lines = ["- (no analytics facts available for this close)"]
    header = (
        "INDICATIVE — reconstructed marks"
        if ctx.frame.mode == MODE_INDICATIVE
        else "strict — stored close"
    )
    intro = "Facts block (the only values you may cite):"
    return f"Mode: {header}\n{intro}\n" + "\n".join(lines)


def build_messages(ctx: GroundingContext, question: str) -> list[ChatMessage]:
    user = (
        f"Frame: {_frame_caption(ctx)}\n\n"
        f"{_facts_block(ctx)}\n\n"
        f"PM question: {question}"
    )
    return [
        ChatMessage(role="system", content=_SYSTEM_RULES),
        ChatMessage(role="user", content=user),
    ]


_SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUPERSCRIPT_TO_ASCII = {
    ord(sup): chr(ord("0") + idx) for idx, sup in enumerate(_SUPERSCRIPT_DIGITS)
}
_SUPERSCRIPT_TO_ASCII[ord("⁻")] = "-"
_SUPERSCRIPT_TO_ASCII[ord("⁺")] = "+"
_SUPERSCRIPT_CLASS = _SUPERSCRIPT_DIGITS + "⁻⁺"

# A scientific-notation token in the house idiom: "<mantissa> × 10<exponent>" where the exponent is
# written with Unicode superscripts (sci_format.py:64) or as an ASCII "^n" / "e n" fallback.
_SCI_PATTERN = re.compile(
    r"(?P<mant>-?\d[\d\s,.]*?)\s*[×x]\s*10\s*"
    rf"(?:\^?\s*(?P<asc>[-+]?\d+)|(?P<sup>[{_SUPERSCRIPT_CLASS}]+))"
)
_PLAIN_PATTERN = re.compile(r"(?<![A-Za-z\d])-?\d[\d\s,.]*")

_FR_UNITS = {
    "zéro": 0, "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4,
    "cinq": 5, "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11,
    "douze": 12, "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
    "vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60,
    "cent": 100, "cents": 100, "mille": 1000,
}
_EN_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100, "thousand": 1000,
}
_FR_TEENS = {
    "dix-sept": 17, "dix-huit": 18, "dix-neuf": 19,
    "vingt-et-un": 21, "trente-et-un": 31,
    "soixante-dix": 70, "quatre-vingt": 80, "quatre-vingts": 80,
    "quatre-vingt-dix": 90,
}
_SPELLED_WORDS = (
    set(_FR_UNITS) | set(_EN_UNITS) | {w for phrase in _FR_TEENS for w in phrase.split("-")}
)
_PERCENT_FR = ("pour cent", "pourcent")
_TOKEN_SPLIT = re.compile(r"[^0-9a-zàâäéèêëïîôöùûüç-]+", re.IGNORECASE)


def _strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch)
    )


def _parse_plain(token: str) -> float | None:
    cleaned = token.strip().rstrip(".").replace(" ", "").replace(",", "")
    if cleaned in ("", "-", ".", "+"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_superscript(raw: str) -> int | None:
    ascii_exp = raw.translate(_SUPERSCRIPT_TO_ASCII)
    try:
        return int(ascii_exp)
    except ValueError:
        return None


def _sci_values(text: str) -> list[float]:
    values: list[float] = []
    for match in _SCI_PATTERN.finditer(text):
        mantissa = _parse_plain(match.group("mant"))
        if mantissa is None:
            continue
        if match.group("asc") is not None:
            exponent: int | None = int(match.group("asc"))
        else:
            exponent = _parse_superscript(match.group("sup"))
        if exponent is None:
            continue
        values.append(mantissa * (10.0**exponent))
    return values


def _spelled_values(text: str) -> list[float]:
    lowered = _strip_accents(text.lower())
    percent = False
    for marker in (*(_strip_accents(p) for p in _PERCENT_FR), "percent"):
        if marker in lowered:
            percent = True
            lowered = lowered.replace(marker, " ")
    tokens = [t for t in _TOKEN_SPLIT.split(lowered) if t]
    teens = {_strip_accents(k): v for k, v in _FR_TEENS.items()}
    units = {
        _strip_accents(k): v for mapping in (_FR_UNITS, _EN_UNITS) for k, v in mapping.items()
    }
    values: list[float] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in teens:
            values.append(float(teens[token]))
            i += 1
            continue
        if token in units:
            value = units[token]
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if value in (100, 1000) and nxt in units and units[nxt] < value:
                value += units[nxt]
                i += 1
            values.append(float(value))
            i += 1
            continue
        i += 1
    if percent:
        return [v / 100.0 for v in values]
    return values


def _spans_consumed_by_sci(text: str) -> list[tuple[int, int]]:
    return [m.span() for m in _SCI_PATTERN.finditer(text)]


def _numeric_values(text: str) -> list[float]:
    values: list[float] = list(_sci_values(text))
    consumed = _spans_consumed_by_sci(text)
    for match in _PLAIN_PATTERN.finditer(text):
        start, end = match.span()
        if any(s <= start and end <= e for s, e in consumed):
            continue
        parsed = _parse_plain(match.group(0))
        if parsed is None:
            continue
        rest = text[end:].lstrip()
        if rest.startswith("%"):
            values.append(parsed / 100.0)
        values.append(parsed)
    values.extend(_spelled_values(text))
    return values


def _matches(value: float, allowed: list[float]) -> bool:
    return any(
        abs(value - candidate) <= _VALUE_TOLERANCE + _VALUE_TOLERANCE * abs(candidate)
        for candidate in allowed
    )


def _allowed_values(ctx: GroundingContext) -> list[float]:
    allowed: list[float] = []
    for fact in ctx.facts:
        allowed.extend(_numeric_values(fact.value_text))
        if fact.raw_value is not None:
            allowed.append(float(fact.raw_value))
    coverage = ctx.frame.coverage
    for count in (coverage.option_rows, coverage.two_sided, coverage.excluded):
        allowed.append(float(count))
    if coverage.two_sided_fraction is not None:
        allowed.append(float(coverage.two_sided_fraction))
    if ctx.frame.close_instant is not None:
        allowed.extend(_numeric_values(ctx.frame.close_instant))
    if ctx.frame.trade_date is not None:
        allowed.extend(_numeric_values(ctx.frame.trade_date.isoformat()))
    return allowed


def ungrounded_numbers(answer: str, ctx: GroundingContext) -> list[str]:
    allowed = _allowed_values(ctx)
    offending: list[str] = []
    for value in _numeric_values(answer):
        if not _matches(value, allowed):
            offending.append(_render_offender(value))
    return offending


def _render_offender(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return repr(value)


def is_grounded(answer: str, ctx: GroundingContext) -> bool:
    return not ungrounded_numbers(answer, ctx)
