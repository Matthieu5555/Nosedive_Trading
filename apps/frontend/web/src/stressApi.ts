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

export interface BasketScenarioGap {
  underlying: string;
  tenor_label: string | null;
  delta_band: string | null;
  reason: string;
}

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
