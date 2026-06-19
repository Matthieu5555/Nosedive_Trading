# 0063 — the guided-tour catalog is read off the live DOM, not a hand-maintained registry

- **Status:** accepted, 2026-06-19 (owner direction, Matthieu: "AI-first design, a deep enough
  module such that whatever change we do the assistant would be aware").
- **Date:** 2026-06-19.
- **Relates to:** [[0058-assistant-grounding-validator-streaming-and-bff-grounding-drop]] (same trust
  contract: the model is grounded on a server/front-supplied catalog and the BFF validates the
  returned highlight against it; this ADR only changes where the *front* gets that catalog from, not
  the validation guarantee, which is untouched).

## Context

The guided-tour assistant points a spotlight at real on-screen elements. The element vocabulary it is
allowed to point at was a closed, hand-maintained list in `apps/frontend/web/src/lib/tour/registry.ts`
(32 anchors: id, route, label, description). Each anchor also had to be placed on a real node via a
`data-tour-id="<id>"` attribute, by hand, in the component. So there were **two sources of truth that
had to be edited in lockstep**: the registry entry and the DOM attribute.

They drifted, exactly as that shape always does:

- The surface **side toggle** (calls / puts / combined, added with the per-side surfaces) got a
  `data-tour-id="market.side-toggle"` in `SurfacePanel.tsx` but was **never added to the registry**.
  Result: the assistant could not point at it, and had the BFF ever returned that id it would have
  been nulled as an "invented" element.
- Conversely a removed panel could leave a dead registry entry the model would try to highlight on a
  node that no longer exists.

The owner asked for an AI-first design: the assistant should stay aware of the UI automatically, so
that *whatever* changes we make to the frontend, the grounding follows with no second list to update.

## Decision

**Collapse to one source of truth at the element, and derive the catalog from the live DOM at guide
time.**

- A single helper, `tourAnchor(id, label, description)` (`lib/tour/anchor.ts`), is spread onto the
  anchor element. It emits three data attributes: `data-tour-id`, `data-tour-label`, `data-tour-desc`.
  The label and description (PM register, the text that grounds the model) now live *at the component*,
  next to the thing they describe.
- `tourCatalog(route)` (`lib/tour/catalog.ts`) reads every `[data-tour-id]` node off the DOM and
  builds the catalog from their data attributes, de-duplicated by id, stamping the caller's current
  route. The `FloatingAssistant` calls it at request time, so the catalog POSTed to the BFF is
  **exactly the set of anchors mounted on the page the user is looking at right now** (plus the nav
  tabs, which are mounted on every page and are the navigation layer for cross-page tours).
- `registry.ts` is deleted. There is no list to keep in sync.

The BFF is unchanged: the request contract is still `{ id, label, description, route }[]`, the prompt
still grounds strictly on the posted catalog, and `_coerce_highlight` still nulls any id not in it.
The trust guarantee from ADR 0058 holds verbatim; only the catalog's provenance changed.

## Consequences

- **No drift.** Add a panel with `tourAnchor(...)` and the assistant knows about it; remove the panel
  and it leaves the catalog. The `market.side-toggle` gap is fixed as a side effect of the migration,
  and a future control cannot regress the same way because there is nowhere to forget to register it.
- **Stronger grounding, not just equal.** The model only ever sees anchors that are actually on
  screen. A control that is conditionally hidden (e.g. the side toggle when per-side data is absent)
  is correctly absent from the catalog, so the assistant will not tell a PM to click something that
  is not there.
- **Cross-page tours still work** because they always did, step by step: each step re-reads the DOM,
  the nav tabs (always mounted, rich descriptions) carry the user to the target page, and the target
  page's anchors appear in the catalog once mounted. No need to know unmounted pages up front.
- **Cost:** the anchor copy is now spread across the components instead of centralized. That is the
  point (the description lives where the element lives), and `lib/tour/catalog.test.ts` covers the
  module's contract (mounted ⇒ in catalog, unmounted ⇒ gone, dedup, graceful degradation).
- **Trade-off not taken:** a static-registry-plus-drift-guard design would also stop drift, but keeps
  the two lists and a test to police them. The owner chose the live-DOM design for maximal automatic
  awareness; this ADR records that choice.
