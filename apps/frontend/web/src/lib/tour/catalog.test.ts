import { afterEach, describe, expect, test } from "vitest";

import { tourAnchor } from "./anchor";
import { tourCatalog } from "./catalog";

// Mount a detached element carrying the given anchor props and return it. Using a real DOM node (not
// a string) is the point of the test: the catalog is whatever is actually mounted, so the test mounts
// things and reads them back exactly the way the assistant loop does at guide time.
function mountAnchor(id: string, label: string, description: string): HTMLElement {
  const el = document.createElement("div");
  const props = tourAnchor(id, label, description);
  for (const [name, value] of Object.entries(props)) el.setAttribute(name, value);
  document.body.appendChild(el);
  return el;
}

afterEach(() => {
  document.body.innerHTML = "";
});

describe("tourCatalog reads the live DOM", () => {
  test("an anchor declared with tourAnchor surfaces as a catalog entry", () => {
    mountAnchor(
      "market.surface",
      "Volatility surface",
      "The 3D implied-volatility surface, vol against moneyness and maturity.",
    );

    const catalog = tourCatalog("/market");

    expect(catalog).toEqual([
      {
        id: "market.surface",
        label: "Volatility surface",
        description: "The 3D implied-volatility surface, vol against moneyness and maturity.",
        route: "/market",
      },
    ]);
  });

  test("only mounted anchors are in the catalog, the core AI-first property", () => {
    // The whole reason this is DOM-derived: an element that is not on screen is not in the catalog,
    // so the assistant can never be told to point at something that is not there. Mount one, assert
    // it is present, remove it, assert it is gone, all without touching a second source of truth.
    const node = mountAnchor("market.side-toggle", "Surface side toggle", "Calls, puts, or both.");
    expect(tourCatalog("/market").map((e) => e.id)).toContain("market.side-toggle");

    node.remove();
    expect(tourCatalog("/market").map((e) => e.id)).not.toContain("market.side-toggle");
  });

  test("the route is stamped from the caller, not the anchor", () => {
    mountAnchor("nav.market", "Market tab", "Opens the Market page.");
    // The same anchor reports whichever route the caller is currently on, since with a DOM-derived
    // catalog every mounted anchor is, by definition, on the page the user is looking at right now.
    expect(tourCatalog("/positions")[0].route).toBe("/positions");
  });

  test("anchors are de-duplicated by id, first occurrence wins", () => {
    mountAnchor("market.smile", "Smile and Greeks", "First copy.");
    mountAnchor("market.smile", "Smile and Greeks", "Second copy.");

    const entries = tourCatalog("/market").filter((e) => e.id === "market.smile");
    expect(entries).toHaveLength(1);
    expect(entries[0].description).toBe("First copy.");
  });

  test("a node missing its label or description degrades instead of throwing", () => {
    const el = document.createElement("div");
    el.setAttribute("data-tour-id", "bare.anchor");
    document.body.appendChild(el);

    expect(tourCatalog("/")).toEqual([
      { id: "bare.anchor", label: "bare.anchor", description: "", route: "/" },
    ]);
  });

  test("an empty page yields an empty catalog", () => {
    expect(tourCatalog("/")).toEqual([]);
  });
});
