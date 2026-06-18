# Guided tour, frozen interface contract

The "guided tour" lets a user ask the assistant "how do I do X?" in plain language. The assistant
replies with short, no-jargon steps, one at a time ("Click Basket up top."), while visually
highlighting that exact on-screen element. The user acts; the app detects the action and the
assistant emits the next step, until the goal is reached.

This document FREEZES every seam the four implementer agents build against. The FOUNDATION agent has
shipped the registry and the guide API types and types this contract. Do not redesign these seams;
build to them.

The most important design point is the trust mechanism. The model must NEVER invent a UI element.
The front owns the registry of real, highlightable anchors (`src/lib/tour/registry.ts`). With each
guide request the front POSTs the relevant catalog (`tourCatalog()`) to the BFF. The BFF grounds the
model strictly on that posted catalog and is told it may only reference those ids. The BFF then
VALIDATES the returned highlight id against the posted catalog; if the id is not present, the BFF
nulls out the highlight. This mirrors the existing `ungrounded_numbers` guard in
`assistant_prompt.py`, and is the navigation analogue of the facts-block guarantee.

---

## 1. The registry (shipped: `src/lib/tour/registry.ts`)

```ts
interface TourAnchor { id: string; route: string; label: string; description: string }
```

- `id`: kebab/dotted. Nav links = `nav.<route-name>`, widgets = `<page>.<widget>`.
- `route`: the route path the anchor lives on. Nav anchors carry the home route `"/"` nominally,
  they exist on every page.
- `label`: short human name, no em dashes.
- `description`: one short plain-English sentence, PM register, no engine jargon. This is the text
  that grounds the model.

Exports: `TOUR_ANCHORS`, `tourAnchorById(id)`, `anchorsForRoute(route)`, `tourCatalog()`.

### Full anchor id list, grouped by page

Nav (always present, top bar):
- `nav.market`, `nav.basket`, `nav.signals`, `nav.strategy`, `nav.risk`, `nav.positions`,
  `nav.operations`

Market (`/`):
- `market.index-picker`, `market.as-of`, `market.scorecard`, `market.price`, `market.surface`,
  `market.mode-toggle`, `market.smile`, `market.dispersion`, `market.coverage`

Basket (`/basket`):
- `basket.underlying`, `basket.templates`, `basket.tabs`

Signals (`/signals`):
- `signals.underlying`

Strategy (`/strategy`):
- `strategy.setup`

Risk Scenarios (`/risk`):
- `risk.portfolio`, `risk.scenarios`

Positions (`/positions`):
- `positions.underlying`

Operations (`/operations`):
- `operations.health`

---

## 2. Anchor placement table (agent C)

Agent C adds ONE attribute per anchor: `data-tour-id="<id>"`. Placement must NOT change behavior,
add only the attribute, change nothing else. Add it to the outermost stable element of the widget
(the one whose bounding rect the Spotlight should ring). Each row quotes the current opening tag so
the insertion is mechanical: add the attribute inside that tag.

### Nav anchors, file `src/App.tsx`

The nav renders one `NavLink` per `ROUTES` entry in a `.map`. There is no per-link literal to edit,
so attach the id by route. In the `NavLink` element (around line 42), add:

```tsx
<NavLink
  key={item.path}
  to={item.path}
  end={item.end}
  data-tour-id={NAV_TOUR_ID[item.path]}
  className={({ isActive }) => (isActive ? "nav-button active" : "nav-button")}
>
```

and define the small map near the top of `App.tsx` (import is fine, but a local const keeps App's
imports flat):

```tsx
const NAV_TOUR_ID: Record<string, string> = {
  "/": "nav.market",
  "/basket": "nav.basket",
  "/signals": "nav.signals",
  "/strategy": "nav.strategy",
  "/risk": "nav.risk",
  "/positions": "nav.positions",
  "/operations": "nav.operations",
};
```

This is the one place where agent C adds a tiny data map rather than a single attribute. It changes
no behavior (the attribute is inert). Keep it; the loop relies on these ids being present on every
page.

### Market anchors, file `src/pages/Market.tsx`

| id | element to tag (current opening tag, add the attribute) |
|---|---|
| `market.index-picker` | `<select aria-label="Index" ...>` (line ~109) |
| `market.as-of` | `<AsOfSelect ... />` (line ~131). AsOfSelect is a component; tag its rendered wrapper, or wrap the call in a `<span data-tour-id="market.as-of">`. Prefer adding `data-tour-id` to the outer element inside `market/marketHeader.tsx`'s `AsOfSelect` if it renders a single stable wrapper; otherwise the span wrapper here. Behavior unchanged either way. |
| `market.scorecard` | `<Scorecards ... />` is inside an `AsyncBlock`. Tag the `<ErrorBoundary label="Scorecards">`'s child wrapper, simplest is to add `data-tour-id="market.scorecard"` to the `<Scorecards ...>` root element in `components/Scorecards.tsx`. |
| `market.price` | `<article className="panel" aria-label={\`${index} daily history\`}>` (line ~218) |
| `market.surface` | `<article className="panel" aria-label={descriptor.subjectHeading}>` (line ~250) |
| `market.mode-toggle` | `<div className="mode-toggle" role="group" aria-label="Surface mode">` in `SurfaceModeToggle` (line ~395) |
| `market.smile` | `<TenorPanel ... />` (line ~310). Tag the `TenorPanel` root element in `components/TenorPanel.tsx`, or wrap the call site's `<ErrorBoundary label="Tenor view">` child. |
| `market.dispersion` | `<article className="panel" aria-label="Dispersion">` (line ~323) |
| `market.coverage` | `<article className="panel" aria-label="Capture coverage">` (line ~350). Tag the article so the ring frames the whole panel including its Show/Hide button. |

Note for `market.scorecard`, `market.smile`, `market.as-of`: these are component instances, not bare
DOM. The cleanest placement is on the component's own root element inside its file, since these
components accept and spread no arbitrary props today. If a component does not spread extra props to
its root, agent C adds the attribute to the JSX root inside that component file. That is the only
permitted edit outside the page files for agent C, and it changes no behavior.

### Basket anchors, file `src/pages/Basket.tsx`

| id | element to tag |
|---|---|
| `basket.underlying` | `<select aria-label="underlying" ...>` (line ~211) |
| `basket.templates` | `<Cluster gap="xs" role="group" aria-label="templates">` (line ~243) |
| `basket.tabs` | `<TabsList className="market-tabs__list max-w-none">` (line ~278) |

### Signals anchor, file `src/pages/Signals.tsx`

| id | element to tag |
|---|---|
| `signals.underlying` | `<select aria-label="Underlying" ...>` (line ~47) |

### Strategy anchor, file `src/pages/Strategy.tsx`

| id | element to tag |
|---|---|
| `strategy.setup` | the `<Card>` wrapping "Backtest setup" (line ~63). Add `data-tour-id` to that `<Card>`; the shadcn `Card` spreads props to its root `div`, so the attribute lands on the DOM node. |

### Risk Scenarios anchors, file `src/pages/RiskScenarios.tsx`

| id | element to tag |
|---|---|
| `risk.portfolio` | `<select id="risk-portfolio" aria-label="Portfolio" ...>` (line ~45) |
| `risk.scenarios` | the `<Card>` wrapping "Named historical scenarios" (line ~77) |

### Positions anchor, file `src/pages/Positions.tsx`

| id | element to tag |
|---|---|
| `positions.underlying` | `<select aria-label="Underlying" ...>` (line ~67) |

### Operations anchor, file `src/pages/Operations.tsx`

| id | element to tag |
|---|---|
| `operations.health` | the `<Card>` wrapping "System health" (line ~44) |

shadcn `Card`, `TabsList`, and the native `select`/`article`/`div`/`Cluster` elements all render to a
DOM node that accepts `data-*`. For native elements and shadcn primitives, add the attribute
directly. For the few project components above (`Scorecards`, `TenorPanel`, `AsOfSelect`), tag the
component's own JSX root.

---

## 3. The guide JSON contract (shipped types in `assistantApi.ts`)

Request body POSTed to `POST /api/assistant/guide`:

```ts
interface GuideRequest {
  goal: string;       // plain-language goal, e.g. "how do I read the smile?"
  route: string;      // route the user is currently on
  completed: string[];// anchor ids already completed this tour
  catalog: { id: string; label: string; description: string; route: string }[]; // tourCatalog()
}
```

Response:

```ts
type GuideExpect = "navigate" | "click" | "none";
interface GuideStep {
  say: string;            // short, no-jargon instruction to show as an assistant message
  highlight: string | null; // a catalog anchor id, or null
  expect: GuideExpect;    // what action advances the step
  done: boolean;          // true once the goal is reached
}
```

### BFF grounding + validation rules (agent B)

1. Build the system prompt to include ONLY the posted `catalog` (id, label, description, route). Tell
   the model: it may reference a highlight ONLY by an id that appears in the catalog; it must emit
   short, plain, no-jargon instructions, one step; it must set `expect` to `"navigate"` when the next
   action is opening a different page (highlight a `nav.*` id), `"click"` when the action is clicking
   the highlighted element on the current page, `"none"` for an informational or terminal step; it
   must set `done: true` only when the goal is reached.
2. The model returns a `GuideStep`. The BFF VALIDATES `highlight`: if it is non-null and NOT one of
   the catalog ids, the BFF sets `highlight` to `null` (the navigation analogue of nulling an
   ungrounded number). It also coerces `expect` to a known value (default `"none"`) and `done` to a
   bool. Use the same shape of guard as `ungrounded_numbers` / `is_grounded` in
   `assistant_prompt.py`, a pure validator over the posted catalog, no network.
3. Lean on the existing OpenRouter client and the existing 502 `assistant_unavailable` error shape
   from `routers/assistant.py`. The guide endpoint does NOT need the facts block or citations.
4. Agent B owns a NEW `guide_prompt.py` (the prompt builder + the highlight validator) plus the new
   `POST /api/assistant/guide` route added to `routers/assistant.py`. Python only, no front edits.

---

## 4. Spotlight component contract (agent A)

File `src/components/Assistant/Spotlight.tsx`, CSS in `src/components/Assistant/Spotlight.css`
(NEW files, agent A owns only these).

Signature:

```tsx
export function Spotlight({ tourId }: { tourId: string | null }): JSX.Element | null
```

Behavior:
- When `tourId` is non-null, resolve the node with
  `document.querySelector('[data-tour-id="' + tourId + '"]')`.
- Scroll it into view: `el.scrollIntoView({ behavior: "smooth", block: "center" })`.
- Render a fixed full-viewport overlay that dims the rest of the screen and draws a pulsing ring
  around the element's bounding rect (`getBoundingClientRect()`).
- Recompute the rect on `scroll` and `resize` (and ideally after the smooth scroll settles); keep the
  ring aligned to the live rect.
- Render nothing (`return null`) when `tourId` is null OR the element is missing. Missing element is
  the safe no-op, never throw.
- The overlay must NOT block clicks on the highlighted element: route pointer events so the dim layer
  is `pointer-events: none` over the hole, or cut the hole so the real element stays clickable (the
  loop's `expect:"click"` depends on the user being able to click the highlighted element).
- Ride existing design tokens (`var(--*)`); no new palette. No em dashes in any string.

---

## 5. AssistantPanel + loop contract (agent D)

Agent D owns `AssistantPanel.tsx`, `assistant.css`, `AssistantContext.tsx`, `FloatingAssistant.tsx`.

Panel chrome:
- Fullscreen toggle: an expand control that adds the class `.assistant-panel--expanded` (near
  fullscreen). A `×` returns it to the docked corner.
- Refresh button: aborts any in-flight request and clears the conversation turns (and stops any
  active tour / clears the spotlight).

The guide loop:
1. Detect a guide intent ("how do I..." in the input, or a "Show me how" affordance). On intent, call
   `askGuide({ goal, route, completed, catalog })` where:
   - `route` is the current path from react-router `useLocation().pathname`,
   - `completed` is the list of anchor ids advanced so far this tour,
   - `catalog` is `tourCatalog()` from `src/lib/tour/registry.ts`.
2. Render `step.say` as an assistant message, and mount `<Spotlight tourId={step.highlight} />`.
3. Advance on the expected action:
   - `expect: "navigate"`: listen for a route change (a `useLocation` change). When the path changes,
     push the highlighted id to `completed` and request the next step.
   - `expect: "click"`: listen for a click on the highlighted element
     (`[data-tour-id="<step.highlight>"]`). On click, advance.
   - Always also render a manual "Next" button so the user can advance without the auto-detect.
4. Repeat until `step.done`, then stop. A "Stop tour" control clears the spotlight (renders
   `<Spotlight tourId={null} />`) and ends the loop at any time.
5. On unmount or refresh, abort the in-flight `askGuide` and clear the spotlight.

`AssistantContext.tsx` already carries the surface frame; agent D may extend it with tour state if
needed, but must not break the existing `AssistantFrameContext` / `EMPTY_FRAME` /
`useAssistantFrame` / `useSetAssistantFrame` exports that `Market.tsx` and the panel depend on.

---

## 6. File ownership map for Phase 2

- Agent A: `src/components/Assistant/Spotlight.tsx`, `Spotlight.css` (NEW only).
- Agent B: `assistant_prompt.py` sibling `guide_prompt.py` (NEW) + edits to `routers/assistant.py`.
  Python only.
- Agent C: the 7 page files + `App.tsx`, adding `data-tour-id` attributes only (plus the tiny
  `NAV_TOUR_ID` map in App.tsx and the few component-root attributes noted in section 2). No behavior
  changes.
- Agent D: `AssistantPanel.tsx`, `assistant.css`, `AssistantContext.tsx`, `FloatingAssistant.tsx`.

Frozen by FOUNDATION, do not edit: `src/lib/tour/registry.ts`, and the guide-type additions in
`src/components/Assistant/assistantApi.ts`.
