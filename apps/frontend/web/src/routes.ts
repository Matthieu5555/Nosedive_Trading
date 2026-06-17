export interface AppRoute {
  readonly path: string;

  readonly label: string;

  readonly heading: string;

  readonly end?: boolean;
}

// The seven top-level tabs, in workflow order: read the market, build a basket, read its signals,
// backtest the line, shock the book, check positions, watch the system. All English. The earlier
// 3-tab consolidation (Données/Risque/Ordres) is retired; the richer Market and Basket pages it
// produced are kept, and Orders' content folds back into Strategy (backtest) + Risk Scenarios
// (reconciliation), which already carry it.
export const ROUTES: readonly AppRoute[] = [
  { path: "/", label: "Market", heading: "Market", end: true },
  { path: "/basket", label: "Basket", heading: "Basket Builder" },
  { path: "/signals", label: "Signals", heading: "Signals" },
  { path: "/strategy", label: "Strategy", heading: "Strategy" },
  { path: "/risk", label: "Risk Scenarios", heading: "Risk Scenarios" },
  { path: "/positions", label: "Positions", heading: "Positions" },
  { path: "/operations", label: "Operations", heading: "Operations" },
] as const;
