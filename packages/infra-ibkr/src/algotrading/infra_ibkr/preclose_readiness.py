"""Pre-close (~18:00) readiness check for the unattended close capture.

A doomed close should be caught *before* it runs, not discovered after it has
silently exited 0. Two conditions decide readiness:

1. The gateway is **authenticated** — probed via ``CpRestSession.authenticated()``
   (a real auth-status round-trip, not a curl), so an expired/dead session is
   surfaced while there is still time to re-auth.
2. The observed **two-sided-quote fraction** is at or above the floor
   (``platform_config`` ``qc_threshold.quote_integrity.min_two_sided_fraction``),
   so a market-closed / last-only snapshot — which would land empty derived grids
   and page at QC — is caught at 18:00.

The decision is a **pure function** (:func:`evaluate_readiness`) so it is trivially
importable and assertable by the test layer; the thin ``__main__`` wires the real
session probe and exits non-zero when not ready.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

# Reason codes — stable strings the operator / tests can match on.
READY = "ready"
NOT_AUTHENTICATED = "not_authenticated"
TWO_SIDED_BELOW_FLOOR = "two_sided_below_floor"
NO_QUOTE_OBSERVATION = "no_quote_observation"


@dataclass(frozen=True, slots=True)
class ReadinessVerdict:
    """Outcome of a pre-close readiness evaluation.

    ``ready`` is the single boolean the caller acts on; ``reasons`` enumerates
    every failing condition (so the operator sees *all* problems at once, not just
    the first), and ``detail`` is a human-readable one-liner for the log/alert.
    """

    ready: bool
    reasons: tuple[str, ...]
    detail: str

    @property
    def exit_code(self) -> int:
        return 0 if self.ready else 1


def evaluate_readiness(
    *,
    authenticated: bool,
    two_sided_fraction: float | None,
    min_two_sided_fraction: float,
) -> ReadinessVerdict:
    """Decide whether the close is safe to run, from already-probed observations.

    Pure: takes the observed gateway auth flag and the observed two-sided fraction
    (``None`` when no quote observation could be made at all) plus the configured
    floor, and returns the verdict. No I/O — the caller does the probing.
    """
    reasons: list[str] = []
    parts: list[str] = []

    if authenticated:
        parts.append("gateway authenticated")
    else:
        reasons.append(NOT_AUTHENTICATED)
        parts.append("gateway NOT authenticated")

    if two_sided_fraction is None:
        reasons.append(NO_QUOTE_OBSERVATION)
        parts.append("no quote observation")
    elif two_sided_fraction < min_two_sided_fraction:
        reasons.append(TWO_SIDED_BELOW_FLOOR)
        parts.append(
            f"two-sided fraction {two_sided_fraction:g} < floor {min_two_sided_fraction:g}"
        )
    else:
        parts.append(
            f"two-sided fraction {two_sided_fraction:g} >= floor {min_two_sided_fraction:g}"
        )

    ready = not reasons
    prefix = "ready" if ready else "NOT READY"
    return ReadinessVerdict(
        ready=ready,
        reasons=tuple(reasons) if reasons else (READY,),
        detail=f"pre-close {prefix}: " + "; ".join(parts),
    )


def probe_two_sided_fraction(  # pragma: no cover
    *, session: object, config: object
) -> float | None:
    """Probe the live two-sided-quote fraction.

    Stub seam: a real probe runs a lightweight snapshot of the current chain and
    reports ``two_sided_count / option_row_count``. Until that lightweight probe
    lands, returning ``None`` makes the readiness check conservatively report
    "no quote observation" rather than fabricate a passing fraction. The pure
    decision logic already treats ``None`` as not-ready.
    """
    return None


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin I/O shim
    """Wire the real gateway probe and report. Exits non-zero when not ready.

    Kept deliberately thin: all decision logic is in :func:`evaluate_readiness`,
    which the test layer drives directly. This shim only does the live I/O the
    pure function cannot.
    """
    import structlog
    from algotrading.core.config import load_platform_config
    from algotrading.core.paths import repo_root
    from algotrading.infra_ibkr.session_factory import build_gateway_session

    log = structlog.get_logger("ibkr.preclose_readiness")
    config = load_platform_config(repo_root() / "configs")
    min_fraction = config.qc_threshold.quote_integrity.min_two_sided_fraction

    # Do not block re-establishing a session here — a pre-close *check* must report,
    # not heal. Probe the current auth status; build_gateway_session(establish=False)
    # gives us the seam-canonical CpRestSession without waiting on a brokerage session.
    _transport, session = build_gateway_session(establish=False)
    try:
        authed = session.authenticated()
    except Exception as exc:  # noqa: BLE001 - any probe failure is "not authenticated"
        log.warning("ibkr.preclose_readiness.auth_probe_failed", error=str(exc))
        authed = False

    fraction = probe_two_sided_fraction(session=session, config=config) if authed else None

    verdict = evaluate_readiness(
        authenticated=authed,
        two_sided_fraction=fraction,
        min_two_sided_fraction=min_fraction,
    )
    log_fn = log.info if verdict.ready else log.error
    log_fn(
        "ibkr.preclose_readiness.verdict",
        ready=verdict.ready,
        reasons=list(verdict.reasons),
        detail=verdict.detail,
        two_sided_fraction=fraction,
        min_two_sided_fraction=min_fraction,
    )
    return verdict.exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main(sys.argv[1:]))
