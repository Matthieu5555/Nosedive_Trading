"""The implied − risk-free spread diagnostic + warn-only QC gate (ADR 0054, RULED 5).

For each `(currency, tenor)` the spread `implied_rate − r(T)` is a first-class, labelled diagnostic,
a funding / dividend / borrow signal. A spread beyond a configured absolute bound is a **flagged
triage record**, never an exception: the default disposition is **WARN** (tune the bound from banked
history later). The parity-implied rate stays the pricing-consistency rate and is never displaced by
this comparison; the external curve is the risk rate. This module computes the diagnostic and its QC
verdict from already-derived inputs — it does no I/O and recomputes no forward.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# QC verdicts. WARN is the shipped default disposition for a breach (warn, do not fail).
QC_OK = "ok"
QC_WARN = "warn"
QC_FAIL = "fail"

# Spread-QC dispositions a breach maps to (the typed-config `spread_qc_disposition`).
DISPOSITION_WARN = "warn"
DISPOSITION_FAIL = "fail"


class SpreadDiagnosticError(ValueError):
    """A spread diagnostic cannot be computed."""


@dataclass(frozen=True, slots=True)
class ImpliedRiskfreeSpread:
    """The `implied_rate − r(T)` diagnostic for one `(currency, tenor)`, with its QC verdict.

    `risk_free_rate` is the external curve evaluated at `maturity_years`; `implied_rate` is the
    parity-implied pricing-consistency rate. `spread = implied_rate − risk_free_rate`. `breached` is
    `abs(spread) > abs_bound`; `qc_status` is the disposition applied to a breach (WARN by default).
    """

    currency: str
    maturity_years: float
    implied_rate: float
    risk_free_rate: float
    spread: float
    abs_bound: float
    breached: bool
    qc_status: str
    label: str = "implied_riskfree_spread"


def implied_riskfree_spread(
    *,
    currency: str,
    maturity_years: float,
    implied_rate: float,
    risk_free_rate: float,
    abs_bound: float,
    disposition: str = DISPOSITION_WARN,
) -> ImpliedRiskfreeSpread:
    """Compute the spread diagnostic + warn-only QC verdict for one `(currency, tenor)`."""
    if not currency.strip():
        raise SpreadDiagnosticError("currency must be non-empty")
    if not (math.isfinite(maturity_years) and maturity_years > 0.0):
        raise SpreadDiagnosticError(
            f"maturity_years must be a finite positive year fraction, got {maturity_years!r}"
        )
    if not (math.isfinite(implied_rate) and math.isfinite(risk_free_rate)):
        raise SpreadDiagnosticError("implied_rate and risk_free_rate must both be finite")
    if not (math.isfinite(abs_bound) and abs_bound >= 0.0):
        raise SpreadDiagnosticError(f"abs_bound must be finite and non-negative, got {abs_bound!r}")
    if disposition not in (DISPOSITION_WARN, DISPOSITION_FAIL):
        raise SpreadDiagnosticError(
            f"disposition must be {DISPOSITION_WARN!r} or {DISPOSITION_FAIL!r}, got {disposition!r}"
        )

    spread = implied_rate - risk_free_rate
    breached = abs(spread) > abs_bound
    breach_status = QC_FAIL if disposition == DISPOSITION_FAIL else QC_WARN
    qc_status = breach_status if breached else QC_OK
    return ImpliedRiskfreeSpread(
        currency=currency,
        maturity_years=maturity_years,
        implied_rate=implied_rate,
        risk_free_rate=risk_free_rate,
        spread=spread,
        abs_bound=abs_bound,
        breached=breached,
        qc_status=qc_status,
    )
