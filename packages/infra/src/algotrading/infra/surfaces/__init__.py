"""Surface engine — solved IV points in, a fitted vol surface out (step 9).

:func:`fit_slice` calibrates one maturity's smile (SVI, or a labeled nonparametric
fallback when sparse) with a butterfly no-arb check; :func:`calendar_violations`
checks calendar no-arb across slices; :func:`interpolate_total_variance` reads the
surface at any maturity. :func:`surface_parameters` and :func:`surface_grid_cells`
project into the stamped contracts; :func:`slice_plot_series` produces raw-vs-fitted
plot data.

    from algotrading.infra.surfaces import fit_slice, surface_parameters, surface_grid_cells
"""

from __future__ import annotations

from .arbitrage import (
    CalendarViolation,
    butterfly_g,
    butterfly_violations,
    calendar_violations,
)
from .fit import (
    METHOD_INSUFFICIENT,
    METHOD_NONPARAMETRIC,
    METHOD_SVI,
    SliceFit,
    SlicePlotSeries,
    SurfaceProjection,
    fit_slice,
    interpolate_total_variance,
    project_surface_fit,
    slice_plot_series,
    surface_grid_cells,
    surface_parameters,
)
from .reporting import (
    SurfaceSliceSummary,
    atm_volatility,
    summarize_surface_parameters,
)
from .svi import (
    MIN_POINTS_FOR_SVI,
    SURFACE_VERSION,
    SviFit,
    SviParams,
    fit_svi,
)

__all__ = [
    "METHOD_INSUFFICIENT",
    "METHOD_NONPARAMETRIC",
    "METHOD_SVI",
    "MIN_POINTS_FOR_SVI",
    "SURFACE_VERSION",
    "CalendarViolation",
    "SliceFit",
    "SlicePlotSeries",
    "SurfaceProjection",
    "SurfaceSliceSummary",
    "SviFit",
    "SviParams",
    "atm_volatility",
    "butterfly_g",
    "butterfly_violations",
    "calendar_violations",
    "fit_slice",
    "fit_svi",
    "interpolate_total_variance",
    "project_surface_fit",
    "slice_plot_series",
    "summarize_surface_parameters",
    "surface_grid_cells",
    "surface_parameters",
]
