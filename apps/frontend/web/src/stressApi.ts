// Typed client for the BFF stress-surface payload — the `surface` view of
// GET /api/risk/scenarios (mirrors serializers.scenario_surface_to_dict; the HTTP shape is
// the seam, keep them in lockstep). Kept in a dedicated module so it composes with api.ts
// without editing it while the basket work (2A) is in flight there; fold these interfaces
// into api.ts when that settles.

// The reshaped surface: sorted shock axes and a spot-major PnL z-grid — scenario_pnl[i][j] is
// the portfolio full-reprice PnL at spot_shock[i] (relative) and vol_shock[j] (additive). A
// null cell is a labelled hole (no persisted scenario for that shock pair — F-BFF-03), never
// a 0.0; has_holes/n_holes summarize them. The dollar PnL carries its unit string; an empty
// basket is a labelled empty surface (empty axes).
export interface StressSurfaceData {
  spot_shock: number[];
  vol_shock: number[];
  scenario_pnl: (number | null)[][];
  scenario_version: string | null;
  unit: string;
  n_cells: number;
  has_holes: boolean;
  n_holes: number;
}

export interface ScenariosResponse {
  portfolio_id: string | null;
  n_cells: number;
  surface: StressSurfaceData;
}

// A basket leg that could not be repriced into the stress surface, named by its grid coordinate
// and a machine-readable reason (no_analytics_row / provider_ambiguous / no_instrument_master /
// no_spot_for_stock_leg) — a labelled gap, never a silent zero.
export interface BasketScenarioGap {
  underlying: string;
  tenor_label: string | null;
  delta_band: string | null;
  reason: string;
}

// POST /api/basket/scenarios — the on-demand full-reprice surface for a composed basket. Same
// `surface` shape as the persisted view (rendered by the StressSurface component), plus the
// worst-case cell and the per-leg gaps. `n_resolved` of `n_legs` legs made it into the reprice.
export interface BasketScenariosResponse {
  basket_id: string;
  trade_date: string;
  underlying: string;
  surface: StressSurfaceData;
  worst_case: {
    spot_shock: number;
    vol_shock: number;
    pnl: number;
    unit: string;
  };
  n_legs: number;
  n_resolved: number;
  gaps: BasketScenarioGap[];
  n_gaps: number;
}
