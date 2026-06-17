export interface AppRoute {
  readonly path: string;

  readonly label: string;

  readonly heading: string;

  readonly end?: boolean;
}

// The three top-level onglets (frontend-3onglets-target-ux): Données → Risque → Ordres. Data first,
// the rest follows. Operations is a secondary utility (still addressable at /operations) and is NOT
// a top-level tab; Signals is dropped (its content lives in the Données scorecards + ρ̄ strip).
export const ROUTES: readonly AppRoute[] = [
  { path: "/", label: "Données", heading: "Données", end: true },
  { path: "/risque", label: "Risque", heading: "Risque" },
  { path: "/ordres", label: "Ordres", heading: "Ordres" },
] as const;
