// The guided-tour anchor registry: the closed, front-owned vocabulary of real, highlightable UI
// elements the assistant is allowed to point at. This is the navigation analogue of the facts-block
// guarantee in assistant_prompt.py: with each guide request the front POSTs the relevant slice of
// this catalog (tourCatalog()) to the BFF, the BFF grounds the model strictly on those ids, and the
// BFF then validates that any returned highlight id is present in the posted catalog, nulling it out
// otherwise. The model can never invent a UI element, because the only ids it ever sees are the ones
// that genuinely exist here and are placed on a real DOM node (data-tour-id="<id>") by agent C.
//
// Pure data plus tiny lookups, house style mirrored from lib/explain.ts. No em dashes in any
// user-facing string (project rule: commas, not em dashes); descriptions are PM register, plain
// English, no engine jargon, since the description is exactly what grounds the model.

export interface TourAnchor {
  // Stable id, kebab/dotted. Nav links are `nav.<route-name>`; widgets are `<page>.<widget>`.
  id: string;
  // The route path the anchor lives on. The nav links live on every route, so they carry the home
  // route "/" as a nominal home; the loop highlights them regardless of the current page.
  route: string;
  // Short human name, e.g. "Surface", "Basket tab". No em dashes.
  label: string;
  // One short plain-English sentence: what the element is, or what clicking it does. This is the
  // grounding text the model reads, so it stays concrete and jargon-free.
  description: string;
}

// The seven top-level navigation tabs. These exist for certain (App.tsx renders one NavLink per
// ROUTES entry), so every one gets an anchor. Each lives in the top bar and is present on every
// page, which is why expect:"navigate" steps target these.
const NAV_ANCHORS: readonly TourAnchor[] = [
  {
    id: "nav.market",
    route: "/",
    label: "Market tab",
    description: "Opens the Market page, where you read what the market is pricing today.",
  },
  {
    id: "nav.basket",
    route: "/",
    label: "Basket tab",
    description: "Opens the Basket Builder, where you compose a book of option legs and shock it.",
  },
  {
    id: "nav.signals",
    route: "/",
    label: "Signals tab",
    description: "Opens the Signals page, the strategy signal readings taken at the close.",
  },
  {
    id: "nav.strategy",
    route: "/",
    label: "Strategy tab",
    description: "Opens the Strategy page, where you backtest a trading line over captured days.",
  },
  {
    id: "nav.risk",
    route: "/",
    label: "Risk Scenarios tab",
    description:
      "Opens Risk Scenarios, where you shock the book and reconcile it against the broker.",
  },
  {
    id: "nav.positions",
    route: "/",
    label: "Positions tab",
    description: "Opens the Positions page, what you own, what it is worth, and your risk.",
  },
  {
    id: "nav.operations",
    route: "/",
    label: "Operations tab",
    description: "Opens the Operations page, system health and whether today's data is in.",
  },
] as const;

// Market widget anchors. These mirror the explainable widgets in lib/explain.ts and the live page
// structure in pages/Market.tsx (index picker, as-of picker, scorecards, price chart, surface and
// its strict/indicative toggle, the tenor smile and Greeks, dispersion, the capture-coverage panel).
const MARKET_ANCHORS: readonly TourAnchor[] = [
  {
    id: "market.index-picker",
    route: "/market",
    label: "Index picker",
    description: "Choose which index you are looking at, like the Euro Stoxx 50.",
  },
  {
    id: "market.ticker-picker",
    route: "/market",
    label: "Ticker picker",
    description:
      "Pick the ticker the whole page follows, the index itself or any of its members like ASML.",
  },
  {
    id: "market.as-of",
    route: "/market",
    label: "As-of picker",
    description:
      "Choose which captured close you are reading, the date the numbers are taken from.",
  },
  {
    id: "market.scorecard",
    route: "/market",
    label: "Indicator scorecards",
    description:
      "The headline indicators at a glance: how rich vol is, the term-structure slope, and more.",
  },
  {
    id: "market.price",
    route: "/market",
    label: "Daily price chart",
    description: "The daily open, high, low and close history for the chosen index.",
  },
  {
    id: "market.surface",
    route: "/market",
    label: "Volatility surface",
    description: "The 3D implied-volatility surface, vol against moneyness and maturity.",
  },
  {
    id: "market.mode-toggle",
    route: "/market",
    label: "Strict and indicative toggle",
    description:
      "Switch the surface between strict, two-sided quotes only, and indicative, which adds one-sided marks as an estimate.",
  },
  {
    id: "market.smile",
    route: "/market",
    label: "Smile and Greeks",
    description: "The smile, implied vol across strikes, with the option Greeks beside it.",
  },
  {
    id: "market.dispersion",
    route: "/market",
    label: "Dispersion strip",
    description:
      "How tightly the index members are expected to move together, the dispersion read.",
  },
  {
    id: "market.coverage",
    route: "/market",
    label: "Capture coverage panel",
    description: "Open this to see how much of the option chain the surface actually rests on.",
  },
] as const;

// Primary anchors for the other six pages, each confirmed against its page file. One reliable
// landing anchor per page (the index/underlying picker or the first headline panel), so the loop can
// always point at something real once a navigate step lands the user on the page.
const BASKET_ANCHORS: readonly TourAnchor[] = [
  {
    id: "basket.underlying",
    route: "/basket",
    label: "Underlying picker",
    description: "Choose the underlying index the basket is built on.",
  },
  {
    id: "basket.templates",
    route: "/basket",
    label: "Strategy templates",
    description: "One click fills the basket with a ready-made shape like a straddle or strangle.",
  },
  {
    id: "basket.tabs",
    route: "/basket",
    label: "Basket workflow tabs",
    description:
      "Move between composing the book, reading it, stressing it, and explaining its P&L.",
  },
] as const;

const SIGNALS_ANCHORS: readonly TourAnchor[] = [
  {
    id: "signals.underlying",
    route: "/signals",
    label: "Underlying picker",
    description: "Choose which underlying's signal readings you want to see.",
  },
] as const;

const STRATEGY_ANCHORS: readonly TourAnchor[] = [
  {
    id: "strategy.setup",
    route: "/strategy",
    label: "Backtest setup",
    description: "Set the window and the trading line's rules before you run a backtest.",
  },
] as const;

const RISK_ANCHORS: readonly TourAnchor[] = [
  {
    id: "risk.portfolio",
    route: "/risk",
    label: "Portfolio picker",
    description: "Choose which portfolio to scope the scenarios and attribution to.",
  },
  {
    id: "risk.scenarios",
    route: "/risk",
    label: "Named scenarios",
    description: "Replay labelled crises like 2008 and COVID-2020 against today's book.",
  },
] as const;

const POSITIONS_ANCHORS: readonly TourAnchor[] = [
  {
    id: "positions.underlying",
    route: "/positions",
    label: "Underlying picker",
    description: "Choose which underlying's open positions and book summary to show.",
  },
] as const;

const OPERATIONS_ANCHORS: readonly TourAnchor[] = [
  {
    id: "operations.health",
    route: "/",
    label: "System health panel",
    description: "One glance at whether services are up and today's data and risk all completed.",
  },
] as const;

export const TOUR_ANCHORS: readonly TourAnchor[] = [
  ...NAV_ANCHORS,
  ...MARKET_ANCHORS,
  ...BASKET_ANCHORS,
  ...SIGNALS_ANCHORS,
  ...STRATEGY_ANCHORS,
  ...RISK_ANCHORS,
  ...POSITIONS_ANCHORS,
  ...OPERATIONS_ANCHORS,
] as const;

const BY_ID: ReadonlyMap<string, TourAnchor> = new Map(
  TOUR_ANCHORS.map((anchor) => [anchor.id, anchor]),
);

// Non-throwing lookup by id. Returns undefined for an id outside the closed vocabulary, the same
// shape of guard the Spotlight uses before it queries the DOM.
export function tourAnchorById(id: string): TourAnchor | undefined {
  return BY_ID.get(id);
}

// Every anchor that lives on a given route. The nav anchors carry the home route "/" and are not
// route-scoped here; callers that want the page widgets for a route pass that route's path.
export function anchorsForRoute(route: string): TourAnchor[] {
  return TOUR_ANCHORS.filter((anchor) => anchor.route === route);
}

// The serializable slice POSTed to the BFF as the grounding catalog. The model only ever sees these
// four fields; the BFF validates any returned highlight against the ids in this list.
export function tourCatalog(): { id: string; label: string; description: string; route: string }[] {
  return TOUR_ANCHORS.map((anchor) => ({
    id: anchor.id,
    label: anchor.label,
    description: anchor.description,
    route: anchor.route,
  }));
}
