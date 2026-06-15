// The single source of truth for the console's user-facing routes. App.tsx builds both the nav
// and the <Routes> table from this list, and the e2e layout/collision net (e2e/layout.spec.ts)
// iterates it so EVERY route is geometry-checked automatically — add a page here once and it is
// wired into the shell AND covered by the collision suite, with no second list to keep in sync.
//
// This is an `app`-level module (see eslint.config.js boundaries): App.tsx (the app shell) is its
// only in-app importer, and app may depend on app. Keep it free of React and api imports so it
// can also be read by the e2e tests without dragging in the runtime.

export interface AppRoute {
  /** The router path and the NavLink target. */
  readonly path: string;
  /** The nav-button label in the top bar. */
  readonly label: string;
  /** The <h1> the page renders — the e2e suite waits on this to know the route is up. */
  readonly heading: string;
  /** Pass `end` to NavLink so "/" is only active on the exact path, not every nested route. */
  readonly end?: boolean;
}

// Order is the nav-button order, left to right. The Orders sketch is deliberately absent: the
// booking chain lives only on Basket (frontend-orders-booking-reconcile, ruling (b)); /orders
// redirects there in App.tsx rather than appearing as a tab.
export const ROUTES: readonly AppRoute[] = [
  { path: "/", label: "Market", heading: "Market", end: true },
  { path: "/basket", label: "Basket", heading: "Basket Builder" },
  { path: "/risk", label: "Risk Scenarios", heading: "Risk Scenarios" },
] as const;
