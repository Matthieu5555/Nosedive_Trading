// The grounding catalog: the slice of UI the assistant is grounded on for a guide step. It is read
// off the live DOM, not a static list, so it is always exactly the set of anchors currently on
// screen (see anchor.ts for why). The front POSTs this with each guide request; the BFF grounds the
// model strictly on these ids and validates any returned highlight against them, nulling an id that
// is not here. So the model can only ever point at an element that genuinely exists right now.

import { TOUR_DESC_ATTR, TOUR_ID_ATTR, TOUR_LABEL_ATTR } from "./anchor";

// One serializable catalog entry, the only fields the model ever sees for an anchor. `route` is the
// page the anchor is mounted on, stamped from the live route at read time: with a DOM-derived
// catalog every entry is on the page the user is currently looking at, including the nav tabs (which
// live on every page), so `route` is honestly "where this is, relative to you now".
export interface TourCatalogEntry {
  id: string;
  label: string;
  description: string;
  route: string;
}

// Read every mounted anchor off the DOM into a catalog. `route` is the caller's current route
// (passed in rather than read from window here, so the caller's router is the single source of the
// path and the function stays trivially testable). Anchors are de-duplicated by id, first occurrence
// wins, since an id should be unique but a defensive dedup keeps the posted catalog clean. An anchor
// missing its label or description falls back to the id and an empty string rather than throwing, so
// a half-applied helper degrades to a still-valid (if terse) entry instead of breaking the guide.
export function tourCatalog(route: string, root: ParentNode = document): TourCatalogEntry[] {
  const seen = new Set<string>();
  const entries: TourCatalogEntry[] = [];
  for (const el of root.querySelectorAll<HTMLElement>(`[${TOUR_ID_ATTR}]`)) {
    const id = el.getAttribute(TOUR_ID_ATTR);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    entries.push({
      id,
      label: el.getAttribute(TOUR_LABEL_ATTR) ?? id,
      description: el.getAttribute(TOUR_DESC_ATTR) ?? "",
      route,
    });
  }
  return entries;
}
