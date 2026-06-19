export interface AppRoute {
  readonly path: string;

  readonly label: string;

  readonly heading: string;

  readonly end?: boolean;
}

// The six top-level tabs, in daily-operator order: start at the system status (the landing page at
// "/"), read the market, check the book you hold, simulate what-ifs on it, backtest the line, then
// drill the raw signals last. All English. Operations owns the index route "/" so the app opens on
// system status; Market lives at "/market". The pages are kept orthogonal: Positions is the real
// book (and its broker reconciliation); Simulate is every what-if, the held book or a composed
// basket, sharing one stress engine. The old separate Basket and Risk Scenarios tabs folded into
// Simulate (their "/basket" and "/risk" paths redirect there); Orders folded into Strategy.
export const ROUTES: readonly AppRoute[] = [
  { path: "/", label: "Operations", heading: "Operations", end: true },
  { path: "/market", label: "Market", heading: "Market" },
  { path: "/positions", label: "Positions", heading: "Positions" },
  { path: "/simulate", label: "Simulate", heading: "Simulate" },
  { path: "/strategy", label: "Strategy", heading: "Strategy" },
  { path: "/signals", label: "Signals", heading: "Signals" },
] as const;
