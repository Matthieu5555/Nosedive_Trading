export interface AppRoute {
  readonly path: string;

  readonly label: string;

  readonly heading: string;

  readonly end?: boolean;
}

// The seven top-level tabs, in daily-operator order: start at the system status (the landing page
// at "/"), read the market, check positions, build/adjust a basket, shock the book, backtest the
// line, then drill the raw signals last. All English. Operations owns the index route "/" so the
// app opens on system status; Market moved to "/market" (the old "/market" -> "/" redirect is
// retired). The earlier 3-tab consolidation (Données/Risque/Ordres) is retired; the richer Market
// and Basket pages it produced are kept, and Orders' content folds back into Strategy (backtest) +
// Risk Scenarios (reconciliation), which already carry it.
export const ROUTES: readonly AppRoute[] = [
  { path: "/", label: "Operations", heading: "Operations", end: true },
  { path: "/market", label: "Market", heading: "Market" },
  { path: "/positions", label: "Positions", heading: "Positions" },
  { path: "/basket", label: "Basket", heading: "Basket Builder" },
  { path: "/risk", label: "Risk Scenarios", heading: "Risk Scenarios" },
  { path: "/strategy", label: "Strategy", heading: "Strategy" },
  { path: "/signals", label: "Signals", heading: "Signals" },
] as const;
