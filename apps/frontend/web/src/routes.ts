export interface AppRoute {
  readonly path: string;

  readonly label: string;

  readonly heading: string;

  readonly end?: boolean;
}

export const ROUTES: readonly AppRoute[] = [
  { path: "/", label: "Market", heading: "Market", end: true },
  { path: "/basket", label: "Basket", heading: "Basket Builder" },
  { path: "/risk", label: "Risk Scenarios", heading: "Risk Scenarios" },
  { path: "/operations", label: "Operations", heading: "Operations" },
  { path: "/signals", label: "Signals", heading: "Signals" },
  { path: "/strategy", label: "Strategy", heading: "Strategy" },
  { path: "/positions", label: "Positions", heading: "Positions" },
] as const;
