import { SURFACE_SIDE_LABELS, type SurfaceSide } from "../../api";
import { tourAnchor } from "../../lib/tour";

// The Combined / Calls / Puts selector, the one canonical segregation control. Calls and puts carry
// genuinely different skew, so each is its own captured view; Combined is the union the page opens on.
// A side the close did not capture is offered DISABLED (the honest "not captured" state), never
// silently swapped. The surface, the Dollar Greeks table and the Price-structure order book all mount
// this same control, so the three segregation toggles look and behave identically.
const SIDES_ORDER: SurfaceSide[] = ["combined", "call", "put"];

export function SideToggle({
  side,
  available,
  perSideServed,
  onChange,
  ariaLabel = "Side",
  anchor,
}: {
  side: SurfaceSide;
  // The sides the close actually captured. A side not in this list renders disabled.
  available: SurfaceSide[];
  // Whether the backend serves the per-side views at all. Drives the honest disabled-title copy:
  // "not captured for this close" (served, this side missing) vs "restart the BFF" (not served yet).
  perSideServed: boolean;
  onChange: (side: SurfaceSide) => void;
  ariaLabel?: string;
  // Optional guided-tour anchor (id, title, body), threaded straight to tourAnchor.
  anchor?: { id: string; title: string; body: string };
}) {
  return (
    <div
      className="mode-toggle"
      role="group"
      aria-label={ariaLabel}
      {...(anchor ? tourAnchor(anchor.id, anchor.title, anchor.body) : {})}
    >
      {SIDES_ORDER.map((option) => {
        const captured = available.includes(option);
        const disabledTitle = perSideServed
          ? `${SURFACE_SIDE_LABELS[option]} not captured for this close`
          : `${SURFACE_SIDE_LABELS[option]} needs the per-side views, restart the BFF to enable`;
        return (
          <button
            key={option}
            type="button"
            className="mode-toggle__option"
            aria-pressed={side === option}
            disabled={!captured}
            title={
              captured
                ? option === "combined"
                  ? "Both wings together, the union read"
                  : `The ${SURFACE_SIDE_LABELS[option].toLowerCase()} wing on its own`
                : disabledTitle
            }
            onClick={() => onChange(option)}
          >
            {SURFACE_SIDE_LABELS[option]}
          </button>
        );
      })}
    </div>
  );
}
