// WIP — the deep module that owns "this feature is not ready yet".
//
// Same shape as the layout primitives: the call site says WHAT (this subtree, or this whole tab, is
// work-in-progress) and the module decides HOW it looks and behaves — dimmed, grayscaled, inert to
// the pointer, marked aria-disabled, and tagged with a corner "WIP" badge that explains itself on
// hover. A call site cannot grey something out "a little bit differently"; the disabled look is a
// property of the system, picked once here, drawn from the one token set in index.css / foundation.css.
//
// Two granularities, one module:
//   <WIP>            wrap any subtree (a panel, a button, a form) to grey it out in place.
//   FEATURE_STATUS   one map that flags whole tabs/pages; App.tsx consults it for nav + routing.

import type { ElementType, ReactNode } from "react";

import { cn } from "../../lib/utils";

export type FeatureStatus = "ready" | "wip";

export interface FeatureFlag {
  readonly status: FeatureStatus;
  /** Shown in the badge tooltip and the placeholder body. Say what's missing, in plain words. */
  readonly reason?: string;
}

// The single source of truth for which whole tabs/pages are work-in-progress. Keyed by route path
// (the same paths as routes.ts). Empty == everything ships. Flip one line to grey a tab in the nav
// and swap its page for a placeholder; flip it back to ship. Nothing is flagged by default.
export const FEATURE_STATUS: Record<string, FeatureFlag> = {};

const READY: FeatureFlag = { status: "ready" };

/** The flag for a route path. Unknown paths are "ready" — you opt INTO wip, never out of it. */
export function featureStatus(path: string): FeatureFlag {
  return FEATURE_STATUS[path] ?? READY;
}

export function isWip(path: string): boolean {
  return featureStatus(path).status === "wip";
}

/**
 * WipTag — the small amber "WIP" pill. Reused by the element wrapper (corner overlay) and by the nav
 * (inline next to a greyed tab label). `reason` becomes the native tooltip so the why is one hover away.
 */
export function WipTag({ reason, className }: { reason?: string; className?: string }) {
  return (
    <span
      className={cn("wip__tag", className)}
      title={reason ?? "Work in progress"}
      aria-label={reason ? `Work in progress: ${reason}` : "Work in progress"}
    >
      WIP
    </span>
  );
}

/**
 * WIP — wrap any subtree to mark it work-in-progress. The content is dimmed + grayscaled and made
 * inert (pointer-events: none, so nothing inside is clickable or focusable through a real pointer),
 * the wrapper is aria-disabled for assistive tech, and a corner WIP badge floats on top carrying the
 * reason. The children render exactly as they would normally — this only veils them, so the user can
 * still see the shape of what's coming.
 */
export function WIP({
  reason,
  children,
  as: Tag = "div",
  className,
  ...rest
}: {
  reason?: string;
  children: ReactNode;
  as?: ElementType;
  className?: string;
  [key: `data-${string}`]: string | undefined;
}) {
  return (
    <Tag className={cn("wip", className)} aria-disabled="true" data-wip="true" {...rest}>
      <WipTag reason={reason} className="wip__badge" />
      <div className="wip__content" inert>
        {children}
      </div>
    </Tag>
  );
}

/**
 * WipPlaceholder — the whole-page stand-in rendered where a wip tab's live page would go. Reached by
 * URL even when the nav tab is greyed out, so it must say plainly that the page isn't ready and why.
 */
export function WipPlaceholder({ title, reason }: { title?: string; reason?: string }) {
  return (
    <div className="wip-placeholder" role="status">
      <WipTag reason={reason} />
      <p className="wip-placeholder__title">{title ? `${title} is a work in progress` : "Work in progress"}</p>
      {reason ? <p className="wip-placeholder__reason">{reason}</p> : null}
    </div>
  );
}
