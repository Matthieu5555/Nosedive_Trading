"""The composite instrument key and the three event timestamps.

The instrument key is the one identifier every table agrees on. It is a tuple of
nine fields (the economic identity of a tradable thing), collapsed to a single
canonical string so it can be a primary-key column and a join key. Two keys with
the same nine fields produce the same string on any machine, in any process —
that determinism is the whole point, so the string is built by hand from the
fields, never from Python's salted ``hash()``.

For an option, all nine fields are present. For an underlying (a stock or index),
``expiry``, ``strike`` and ``option_right`` have no meaning and are ``None``; the
canonical string writes them as an empty slot so an underlying and its options
never collide.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Option right is one of exactly these two values; anything else is a bad key.
OPTION_RIGHTS = ("C", "P")

# The three timestamps every raw event carries, defined once so no module invents
# its own spelling:
#   exchange_ts  — when the exchange says the event happened.
#   receipt_ts   — when our process first received it.
#   canonical_ts — the single time used for ordering and as-of reads.
EVENT_TIMESTAMP_FIELDS = ("exchange_ts", "receipt_ts", "canonical_ts")


@dataclass(frozen=True, slots=True)
class InstrumentKey:
    """The economic identity of one tradable instrument.

    Strike and multiplier are real numbers, never strings. ``expiry``,
    ``strike`` and ``option_right`` are ``None`` for a non-option underlying and
    required for an option.
    """

    underlying_symbol: str
    security_type: str
    exchange: str
    currency: str
    multiplier: float
    broker_contract_id: str
    expiry: date | None = None
    strike: float | None = None
    option_right: str | None = None

    def is_option(self) -> bool:
        """True when this key names an option (has expiry, strike and right)."""
        return self.expiry is not None

    def canonical(self) -> str:
        """Return the deterministic string form used as ``instrument_key``.

        The format is a fixed pipe-joined field order. Empty slots are written
        for the option-only fields when this is an underlying, so the layout is
        the same width for every instrument and parsing is unambiguous.
        """
        strike = "" if self.strike is None else format(self.strike, ".10g")
        expiry = "" if self.expiry is None else self.expiry.isoformat()
        right = self.option_right or ""
        return "|".join(
            (
                self.underlying_symbol,
                self.security_type,
                self.exchange,
                self.currency,
                format(self.multiplier, ".10g"),
                self.broker_contract_id,
                expiry,
                strike,
                right,
            )
        )
