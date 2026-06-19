// The guided-tour anchor: the single declaration site for a real, highlightable UI element the
// assistant is allowed to point at. This is the AI-first half of the assistant's trust contract.
//
// Old design (registry.ts, now gone) kept the catalog of anchors in one hand-maintained file that
// had to be edited in lockstep with the data-tour-id attributes scattered across components. The two
// drifted: a new control (the surface side toggle) got a data-tour-id in the DOM but was never added
// to the registry, so the assistant could not point at it, and a removed panel could leave a dead
// registry entry the model would try to highlight on an element that no longer exists.
//
// Here the anchor describes itself in one place, at the element. tourAnchor(id, label, description)
// spreads three data-* attributes onto the node, and the catalog (catalog.ts) is read back off the
// live DOM at guide time. There is no second list to keep in sync: whatever is on screen IS the
// catalog the model is grounded on, and the BFF still validates the returned highlight against it.
// Add a panel with tourAnchor(...) and the assistant knows about it; remove the panel and it is gone.
//
// House style: no em dashes in any user-facing string (project rule: commas, not em dashes). The
// description is PM register, plain English, no engine jargon, because it is exactly the text that
// grounds the model.

// The three attribute names, named once so the writer (here) and the reader (catalog.ts, Spotlight)
// can never disagree on a literal.
export const TOUR_ID_ATTR = "data-tour-id";
export const TOUR_LABEL_ATTR = "data-tour-label";
export const TOUR_DESC_ATTR = "data-tour-desc";

// The props object spread onto an anchor element. Keyed by the data-* attribute names so it drops
// straight onto any host element: <article {...tourAnchor("market.surface", "Volatility surface",
// "The 3D implied-volatility surface, vol against moneyness and maturity.")}>.
export interface TourAnchorProps {
  [TOUR_ID_ATTR]: string;
  [TOUR_LABEL_ATTR]: string;
  [TOUR_DESC_ATTR]: string;
}

// Declare an anchor at its element. `id` is the stable kebab/dotted id the model highlights and the
// Spotlight rings (nav links are `nav.<route-name>`, widgets are `<page>.<widget>`). `label` is the
// short human name; `description` is one plain-English sentence saying what the element is or what
// acting on it does. Both label and description travel to the model verbatim through the catalog.
export function tourAnchor(id: string, label: string, description: string): TourAnchorProps {
  return {
    [TOUR_ID_ATTR]: id,
    [TOUR_LABEL_ATTR]: label,
    [TOUR_DESC_ATTR]: description,
  };
}
