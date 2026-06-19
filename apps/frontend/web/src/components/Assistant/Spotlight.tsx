import "./Spotlight.css";

import { type JSX, useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { TOUR_ID_ATTR } from "../../lib/tour";

/* The Spotlight is the visual half of the guided tour: while the assistant says "Click Basket up
   top", this rings the one real on-screen element it pointed at. It NEVER invents geometry, it reads
   the live `getBoundingClientRect()` of the node the front already registered, so the ring can only
   land on a real, highlightable anchor (the trust mechanism from the contract).

   It is deliberately a thin, defensive component:
   - given a null tourId, or an id that resolves to nothing, it renders nothing and never throws,
     because the loop may hand it a step whose highlight is null or whose anchor is on another page;
   - it must never block the click it is inviting (the loop's `expect:"click"` advances on a real
     click of the highlighted element), so the dim is painted as four panels AROUND the hole, leaving
     the element's own rect uncovered and fully clickable. */

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
}

function readRect(tourId: string): Rect | null {
  // The querySelector mirrors the anchor placement contract: tourAnchor(...) puts one data-tour-id on
  // the widget's outermost stable element. Escaping is unnecessary, the ids are kebab/dotted literals
  // declared in the components themselves, never user input.
  const el = document.querySelector<HTMLElement>(`[${TOUR_ID_ATTR}="${tourId}"]`);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return { top: r.top, left: r.left, width: r.width, height: r.height };
}

export function Spotlight({ tourId }: { tourId: string | null }): JSX.Element | null {
  const [rect, setRect] = useState<Rect | null>(null);

  useEffect(() => {
    if (tourId === null) {
      // Clear any stale rect so a null step paints nothing rather than freezing the last ring.
      setRect(null);
      return;
    }

    // Bring the anchor into view, then keep the ring glued to wherever it lands. The first read can
    // happen before the smooth scroll settles, so we also re-measure on a couple of animation frames
    // and a short timeout, which covers both the scroll animation and any late layout shift.
    const target = document.querySelector<HTMLElement>(`[${TOUR_ID_ATTR}="${tourId}"]`);
    target?.scrollIntoView({ behavior: "smooth", block: "center" });

    const measure = () => setRect(readRect(tourId));
    measure();

    const raf1 = requestAnimationFrame(() => {
      const raf2 = requestAnimationFrame(measure);
      // Stash the inner id on the outer one so cleanup can cancel whichever is still pending.
      (raf1 as unknown as { inner?: number }).inner = raf2;
    });
    // The smooth scroll typically settles within a few hundred ms; re-measure once after it should be done.
    const settle = window.setTimeout(measure, 350);

    window.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);

    return () => {
      window.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
      cancelAnimationFrame(raf1);
      const inner = (raf1 as unknown as { inner?: number }).inner;
      if (inner !== undefined) cancelAnimationFrame(inner);
      window.clearTimeout(settle);
    };
  }, [tourId]);

  // Safe no-op: null step, or an anchor that is not on this page, paints nothing.
  if (tourId === null || rect === null) return null;

  // The four dim panels frame the hole. Each is `pointer-events: none` so even the dim never eats a
  // click, and the hole itself is simply unpainted, so the real element underneath stays fully
  // interactive. The ring is a non-interactive outline sitting exactly on the rect.
  const overlay = (
    <div className="tour-spotlight" data-testid="tour-spotlight" aria-hidden="true">
      <div
        className="tour-spotlight__dim"
        style={{ top: 0, left: 0, right: 0, height: rect.top }}
      />
      <div
        className="tour-spotlight__dim"
        style={{ top: rect.top + rect.height, left: 0, right: 0, bottom: 0 }}
      />
      <div
        className="tour-spotlight__dim"
        style={{ top: rect.top, left: 0, width: rect.left, height: rect.height }}
      />
      <div
        className="tour-spotlight__dim"
        style={{ top: rect.top, left: rect.left + rect.width, right: 0, height: rect.height }}
      />
      <div
        className="tour-spotlight__ring"
        data-testid="tour-spotlight-ring"
        style={{
          top: rect.top,
          left: rect.left,
          width: rect.width,
          height: rect.height,
        }}
      />
    </div>
  );

  // Portal to the body so the ring sits above page content regardless of where the loop mounts it.
  return createPortal(overlay, document.body);
}
