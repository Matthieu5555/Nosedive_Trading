// Layout primitives — the deep module that owns spacing, grouping and overflow.
//
// Pages compose these and NEVER write a raw px margin or a hand-picked gap. A call site says WHAT it
// wants (a vertical stack with medium rhythm, a row of controls that wraps, a responsive grid, a
// wide thing that must not push the page) and the primitive decides the pixels, drawing every value
// from the one spacing scale in styles/foundation.css. The interface is the t-shirt size; the
// implementation (flex/grid/overflow rules, the min-width:0 that stops cells overflowing) is hidden.
//
// This is the structural answer to the owner's demand: spacing is a property of the system, not a
// decision an LLM makes per element. You cannot get it wrong from the call site, because the call
// site cannot express a wrong value.

import type { CSSProperties, ElementType, ReactNode } from "react";

import { cn } from "../../lib/utils";

/** The only legal spacing steps. Maps to the --space-* tokens. */
export type Space = "none" | "3xs" | "2xs" | "xs" | "sm" | "md" | "lg" | "xl" | "2xl" | "3xl";

const SPACE_VAR: Record<Space, string> = {
  none: "var(--space-0)",
  "3xs": "var(--space-3xs)",
  "2xs": "var(--space-2xs)",
  xs: "var(--space-xs)",
  sm: "var(--space-sm)",
  md: "var(--space-md)",
  lg: "var(--space-lg)",
  xl: "var(--space-xl)",
  "2xl": "var(--space-2xl)",
  "3xl": "var(--space-3xl)",
};

interface BaseProps {
  children?: ReactNode;
  className?: string;
  /** Render as a different tag (e.g. "section", "ul") without losing the layout behaviour. */
  as?: ElementType;
  /** ARIA role pass-through (e.g. role="group" on a control cluster). */
  role?: string;
  /** Pass-through for aria-* / data-* attributes; never used for spacing. */
  [key: `aria-${string}`]: string | undefined;
  [key: `data-${string}`]: string | undefined;
}

/**
 * Stack — vertical rhythm. ONE gap (from the scale) owns all spacing between children; the children
 * carry no margins. Replaces the per-page pile of competing `margin-top: 16px` / `margin: 14px 0`
 * rules that made spacing look random.
 */
export function Stack({
  gap = "md",
  align,
  as: Tag = "div",
  className,
  children,
  ...rest
}: BaseProps & { gap?: Space; align?: CSSProperties["alignItems"] }) {
  return (
    <Tag
      className={cn("l-stack", className)}
      style={{ "--l-gap": SPACE_VAR[gap], alignItems: align } as CSSProperties}
      {...rest}
    >
      {children}
    </Tag>
  );
}

/**
 * Cluster — horizontal grouping that WRAPS instead of colliding. Items keep the gap on both axes,
 * so two controls can never end up stuck together, and a too-wide row wraps rather than overflows.
 */
export function Cluster({
  gap = "sm",
  align = "center",
  justify = "flex-start",
  as: Tag = "div",
  className,
  children,
  ...rest
}: BaseProps & {
  gap?: Space;
  align?: CSSProperties["alignItems"];
  justify?: CSSProperties["justifyContent"];
}) {
  return (
    <Tag
      className={cn("l-cluster", className)}
      style={
        { "--l-gap": SPACE_VAR[gap], "--l-align": align, "--l-justify": justify } as CSSProperties
      }
      {...rest}
    >
      {children}
    </Tag>
  );
}

/**
 * Grid — responsive auto-fit columns. Each track is minmax(min, 1fr) with a 0 floor inside, so a
 * cell can never overflow its column. Caller picks a `min` column width; the count is automatic.
 */
export function Grid({
  gap = "md",
  min = "240px",
  as: Tag = "div",
  className,
  children,
  ...rest
}: BaseProps & { gap?: Space; min?: string }) {
  return (
    <Tag
      className={cn("l-grid", className)}
      style={{ "--l-gap": SPACE_VAR[gap], "--l-min": min } as CSSProperties}
      {...rest}
    >
      {children}
    </Tag>
  );
}

/**
 * Center — both-axis centering for a single child (an empty state, a spinner, a lone icon). Replaces
 * the hand-rolled `display:flex;align-items:center;justify-content:center` that gets copy-pasted onto
 * a div every time something needs to sit in the middle of the space it is given.
 */
export function Center({ as: Tag = "div", className, children, ...rest }: BaseProps) {
  return (
    <Tag className={cn("l-center", className)} {...rest}>
      {children}
    </Tag>
  );
}

/**
 * Frame — the page measure: a max-width content column, horizontally centered, with the page padding.
 * Stops ragged full-bleed layouts where content runs edge to edge on a wide screen. `measure` widens
 * or narrows the column (any CSS length); left unset, the CSS default applies.
 */
export function Frame({
  measure,
  as: Tag = "div",
  className,
  children,
  ...rest
}: BaseProps & { measure?: string }) {
  return (
    <Tag
      className={cn("l-frame", className)}
      style={measure ? ({ "--measure": measure } as CSSProperties) : undefined}
      {...rest}
    >
      {children}
    </Tag>
  );
}

/**
 * Panel — a surface/card that owns its own padding AND stacks its children with one internal rhythm,
 * so card contents are evenly spaced for free and never carry their own margins. The card is the unit
 * a PM actually reads, so its surface is structurally well-spaced. `gap` overrides the internal
 * vertical rhythm (from the scale); left unset, the CSS default --panel-gap applies.
 */
export function Panel({
  gap,
  as: Tag = "div",
  className,
  children,
  ...rest
}: BaseProps & { gap?: Space }) {
  return (
    <Tag
      className={cn("l-panel", className)}
      style={gap ? ({ gap: SPACE_VAR[gap] } as CSSProperties) : undefined}
      {...rest}
    >
      {children}
    </Tag>
  );
}

/**
 * Scroll — contains a wide table or chart AT THE SOURCE. Whatever is inside scrolls within this box;
 * the page width stays bounded. Wrap every bare table and every chart canvas in this so a wide
 * payload can never push the page sideways (the Market/Signals horizontal-scroll bug, contained
 * structurally instead of patched per page).
 */
export function Scroll({
  as: Tag = "div",
  className,
  children,
  label,
  ...rest
}: BaseProps & { label?: string }) {
  return (
    <Tag
      className={cn("l-scroll", className)}
      role={label ? "region" : undefined}
      aria-label={label}
      tabIndex={label ? 0 : undefined}
      {...rest}
    >
      {children}
    </Tag>
  );
}
