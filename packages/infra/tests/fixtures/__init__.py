"""The shared, immutable fixture library.

Named option chains (the rogues' gallery) live in :mod:`fixtures.library`; the
known-answer generators in :mod:`fixtures.synthetic`; one valid baseline record
per table in :mod:`fixtures.records`. Other workstreams import these by name so
edge-case tests bind to one curated home, never to ad-hoc inline literals.
"""

from __future__ import annotations

from .library import (
    ALL_FIXTURES,
    fixture_names,
    get_fixture,
    make_option,
    make_underlying,
)
from .quotes import ChainFixture, OptionQuoteFixture
from .records import baseline_records
from .synthetic import (
    SyntheticPoint,
    SyntheticSurface,
    black_call,
    black_put,
    build_synthetic_surface,
    parity_forward,
    svi_total_variance,
)

__all__ = [
    "ALL_FIXTURES",
    "ChainFixture",
    "OptionQuoteFixture",
    "SyntheticPoint",
    "SyntheticSurface",
    "baseline_records",
    "black_call",
    "black_put",
    "build_synthetic_surface",
    "fixture_names",
    "get_fixture",
    "make_option",
    "make_underlying",
    "parity_forward",
    "svi_total_variance",
]
