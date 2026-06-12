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
