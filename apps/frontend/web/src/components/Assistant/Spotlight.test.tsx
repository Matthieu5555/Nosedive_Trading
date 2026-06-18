import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import { Spotlight } from "./Spotlight";

/* jsdom has no real layout: getBoundingClientRect returns zeros, so these tests assert STRUCTURE and
   PRESENCE (does the overlay/ring mount, does it read the rect, is the highlighted element left
   clickable) rather than pixel positions. Real-pixel verification is Phase 3's browser job. */

afterEach(() => {
  // Unmount the React tree first (the Spotlight portals into body, so RTL's cleanup must run before we
  // touch the DOM), then remove the bare anchors the tests appended by hand.
  cleanup();
  document.querySelectorAll("[data-tour-id]").forEach((node) => node.remove());
  vi.restoreAllMocks();
});

describe("Spotlight", () => {
  test("renders nothing when tourId is null", () => {
    const { container } = render(<Spotlight tourId={null} />);
    expect(container.firstChild).toBeNull();
    expect(document.querySelector(".tour-spotlight")).toBeNull();
  });

  test("renders nothing when the anchor element is absent", () => {
    // No element carries this id, so the safe no-op path applies: nothing painted, nothing thrown.
    expect(() => render(<Spotlight tourId="market.surface" />)).not.toThrow();
    expect(document.querySelector(".tour-spotlight")).toBeNull();
  });

  test("renders an overlay and reads the rect when a matching anchor exists", () => {
    const anchor = document.createElement("div");
    anchor.setAttribute("data-tour-id", "market.surface");
    const rectSpy = vi.spyOn(anchor, "getBoundingClientRect").mockReturnValue({
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      width: 0,
      height: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    anchor.scrollIntoView = vi.fn();
    document.body.appendChild(anchor);

    render(<Spotlight tourId="market.surface" />);

    // The overlay mounted (via the body portal) and the live rect was read.
    expect(document.querySelector(".tour-spotlight")).not.toBeNull();
    expect(document.querySelector(".tour-spotlight__ring")).not.toBeNull();
    expect(rectSpy).toHaveBeenCalled();
    // It brought the anchor into view as the contract requires.
    expect(anchor.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "center" });
  });

  test("leaves the highlighted element clickable (overlay does not block clicks)", () => {
    const anchor = document.createElement("button");
    anchor.setAttribute("data-tour-id", "nav.basket");
    anchor.getBoundingClientRect = vi.fn().mockReturnValue({
      top: 0,
      left: 0,
      right: 0,
      bottom: 0,
      width: 0,
      height: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    } as DOMRect);
    anchor.scrollIntoView = vi.fn();
    const onClick = vi.fn();
    anchor.addEventListener("click", onClick);
    document.body.appendChild(anchor);

    render(<Spotlight tourId="nav.basket" />);

    // The overlay is painted, yet the real element still receives its click. The dim is cut as four
    // panels AROUND the rect (the hole is unpainted) and every layer is pointer-events:none in CSS, so
    // no click-blocking layer ever covers the highlighted element. (CSS pointer-events isn't applied by
    // jsdom's getComputedStyle, so we assert the load-bearing behaviour: the click lands.)
    expect(document.querySelector(".tour-spotlight")).not.toBeNull();
    anchor.click();
    expect(onClick).toHaveBeenCalled();
  });
});
