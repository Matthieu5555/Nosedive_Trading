import { type ReactNode, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface InfoDotProps {
  label: string;
  body: ReactNode;
  className?: string;
}

// A fixed-viewport coordinate for the open tooltip. Computed from the dot's screen rect so the
// tooltip can be rendered in a portal at the document body, escaping every `overflow: hidden` and
// stacking context an ancestor card imposes (the bug: the scorecard grid clips to its rounded
// border, so an absolutely-positioned tooltip was hidden BEHIND its own card). Fixed + portal means
// the tooltip always floats on top, fully visible, wherever the dot sits.
interface Anchor {
  left: number;
  top: number;
}

export function InfoDot({ label, body, className }: InfoDotProps) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<Anchor | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const tooltipId = useId();

  // Position the tooltip just below-left of the dot, in viewport coordinates, then nudge it back
  // on-screen if it would spill off the right edge. Recomputed every time it opens so a scrolled or
  // resized page never strands the tooltip away from its dot.
  useLayoutEffect(() => {
    if (!open || !buttonRef.current) return;
    const rect = buttonRef.current.getBoundingClientRect();
    const margin = 8;
    const maxWidth = 280;
    let left = rect.left;
    if (left + maxWidth > window.innerWidth - margin) {
      left = Math.max(margin, window.innerWidth - margin - maxWidth);
    }
    setAnchor({ left, top: rect.bottom + 6 });
  }, [open]);

  if (body === null || body === undefined || body === "" || body === false) {
    return null;
  }

  function show() {
    setOpen(true);
  }
  function hide() {
    setOpen(false);
  }

  return (
    <span className={className ? `info-dot-wrap ${className}` : "info-dot-wrap"}>
      <button
        ref={buttonRef}
        type="button"
        className="info-dot"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? tooltipId : undefined}
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(event) => {
          if (event.key === "Escape") hide();
        }}
      >
        <span aria-hidden="true">ⓘ</span>
      </button>
      {open && anchor
        ? createPortal(
            <span
              id={tooltipId}
              role="tooltip"
              className="info-tooltip"
              style={{ left: anchor.left, top: anchor.top }}
            >
              {body}
            </span>,
            document.body,
          )
        : null}
    </span>
  );
}
