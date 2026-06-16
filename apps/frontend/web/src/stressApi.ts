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

// One named historical stress scenario (2008, covid-2020, …): a single labelled compound
// full-reprice shock, its book-summed P&L, and the shocks that define it. `n_legs` is how many
// position legs contributed to the sum. Mirrors named_scenarios_to_list on the BFF.
export interface NamedScenario {
  scenario_id: string;
  label: string;
  spot_shock: number;
  vol_shock: number;
  rate_shock: number | null;
  scenario_pnl: number;
  scenario_version: string;
  n_legs: number;
  unit: string;
}

export interface ScenariosResponse {
  portfolio_id: string | null;
  n_cells: number;
  surface: StressSurfaceData;
  // Additive (F-RISK): empty list on an unconfigured/parametric-only grid, so the existing
  // surface contract is byte-identical when there are no named scenarios.
  named?: NamedScenario[];
  n_named?: number;
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
