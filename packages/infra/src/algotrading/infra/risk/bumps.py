"""The one place finite-difference bump sizes live.

Both the Greeks central-difference cross-check and the scenario engine's local
(Taylor) approximation draw their perturbation sizes from a single versioned
:class:`BumpSpec`. That is deliberate: the classic hidden error in a risk system
is two modules each picking their own bump, so risk and scenarios disagree for
reasons that have nothing to do with economics. The blueprint calls this out
directly (``documentation/blueprint/05-math-notes.md`` §4: "A common source of
hidden error is inconsistent bump sizing across modules, which causes the risk
engine and scenario engine to disagree for reasons unrelated to economics"), and
``tasks/TESTING.md`` makes "Greeks and the scenario engine draw the bump from one
shared versioned source" a required test. Versioning the spec means a change to a
bump is a deliberate, reviewable bump of ``BUMP_VERSION``, not a silent edit buried
in one module.

Units, stated once:

* ``spot_first_rel`` / ``spot_second_rel`` are *relative* spot perturbations
  (a fraction of spot), so the absolute bump scales with the underlying. First
  order (delta) wants a small bump; second order (gamma) wants a larger one to
  keep the differenced quantity above float noise — hence two sizes.
* ``vol_abs`` is an *additive* perturbation in vol units (``0.20`` is 20% vol, so
  ``vol_abs = 1e-5`` is a hundredth of a vol point).
* ``time_abs`` is an additive perturbation in years.
"""

from __future__ import annotations

from dataclasses import dataclass

# Bump only on a real change to a perturbation size, so the version tracks the
# numbers and a scenario report's bump provenance stays meaningful.
BUMP_VERSION = "risk-bumps-1.0.0"


@dataclass(frozen=True, slots=True)
class BumpSpec:
    """Versioned finite-difference perturbation sizes, shared across the engine.

    Frozen, so the shared instance cannot be mutated out from under one caller by
    another. Construct your own only to test a different bump; production code uses
    :data:`DEFAULT_BUMPS`.
    """

    version: str
    spot_first_rel: float
    spot_second_rel: float
    vol_abs: float
    time_abs: float

    def spot_first(self, spot: float) -> float:
        """Absolute spot bump for a first-order (delta) central difference."""
        return self.spot_first_rel * spot

    def spot_second(self, spot: float) -> float:
        """Absolute spot bump for a second-order (gamma) central difference."""
        return self.spot_second_rel * spot


# The shared production bumps. Sizes chosen so a central difference of the pricer's
# price reproduces its analytic Greek to the tolerance pinned in the cross-check
# test (derived from an independent engine, not guessed): 1e-6 * spot for delta,
# 1e-4 * spot for gamma, 1e-5 vol for vega, 1e-5 yr for theta.
DEFAULT_BUMPS = BumpSpec(
    version=BUMP_VERSION,
    spot_first_rel=1e-6,
    spot_second_rel=1e-4,
    vol_abs=1e-5,
    time_abs=1e-5,
)
