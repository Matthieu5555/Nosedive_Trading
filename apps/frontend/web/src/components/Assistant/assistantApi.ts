import { postJson } from "../../api";

export type AssistantMode = "strict" | "indicative";

// The active surface frame the panel posts with every question, so the assistant grounds on the
// SAME (subject · as-of · mode) the page is showing — it can never describe a different frame than
// the chart. The BFF resolves the close instant server-side from the index registry (the venue
// time-of-day + zone, e.g. "17:30 CEST" for SX5E's OESX settlement, honest per-date); the front
// never re-derives it, it only renders what the frame carries back, paired with the trade_date.
export interface AssistantFrame {
  underlying: string;
  trade_date: string;
  run_id: string | null;
  mode: AssistantMode;
  // Filled in by the BFF on the way back: the resolved human close instant and the coverage clause,
  // so the answer wears the same provenance caption the status line shows. Null until grounded.
  close_instant: string | null;
  coverage_label: string | null;
}

// One quoted number the assistant is allowed to surface, lifted verbatim from the server-built
// facts block (already run through the house sci/sciUnit idiom) — never free-text the model wrote.
// The `id` ties back to a copy-map entry so the panel can render the "what is this" gloss beside it.
export interface AssistantCitation {
  id: string;
  label: string;
  value: string;
  source: string;
}

export interface AssistantRequest {
  question: string;
  underlying: string;
  trade_date: string;
  run_id?: string | null;
  mode?: AssistantMode;
  element_id?: string | null;
}

// The grounded contract. `grounded=false` is the honest-gap case: the question needed a number the
// facts block didn't carry, so `answer` is the loud "I won't invent it" copy and `citations` is
// empty — never a fabricated value. `frame` echoes the resolved subject · close · mode · coverage.
export interface AssistantResponse {
  answer: string;
  citations: AssistantCitation[];
  grounded: boolean;
  frame: AssistantFrame;
}

export async function askAssistant(
  body: AssistantRequest,
  signal?: AbortSignal,
): Promise<AssistantResponse> {
  return postJson<AssistantResponse>("/api/assistant", body, signal);
}

// --- Guided tour ---------------------------------------------------------------------------------
//
// The guide loop. A user asks "how do I do X?" in plain language; the assistant answers with short,
// no-jargon steps, one at a time, while visually highlighting the exact on-screen element to act on.
//
// The trust contract (the navigation analogue of the facts-block guarantee): the model may NEVER
// invent a UI element. The front reads the catalog of real, highlightable anchors off the live DOM
// (lib/tour, tourCatalog()) and POSTs it with every guide request, so the catalog is exactly what is
// on screen. The BFF grounds the model strictly on that posted catalog and is told it may only
// reference those ids. The BFF then VALIDATES the returned highlight id against the posted catalog;
// if the id is not present, the BFF nulls out the highlight (mirrors the ungrounded_numbers guard in
// assistant_prompt.py). So a non-null `highlight` is always a real anchor id the front can resolve to
// a data-tour-id node.

// What action advances the step. "navigate" waits for a route change, "click" waits for a click on
// the highlighted element, "none" is a terminal or informational step advanced by the manual Next.
export type GuideExpect = "navigate" | "click" | "none";

export interface GuideStep {
  // The short, no-jargon instruction to show as an assistant message, e.g. "Click Basket up top."
  say: string;
  // A TourAnchor id to highlight, or null for no highlight. Guaranteed by the BFF to be either null
  // or an id present in the catalog this request posted, never a fabricated element.
  highlight: string | null;
  // The action the loop waits for before asking for the next step.
  expect: GuideExpect;
  // True once the goal is reached; the loop stops and clears the spotlight.
  done: boolean;
}

export interface GuideRequest {
  // The user's plain-language goal, e.g. "how do I read the smile?".
  goal: string;
  // The route the user is currently on, so the next step can build on where they are.
  route: string;
  // Anchor ids already completed this tour, so the model advances rather than repeating a step.
  completed: string[];
  // The grounding catalog, the serializable slice of the registry (tourCatalog()). The BFF grounds
  // on this and validates the returned highlight against the ids in it.
  catalog: { id: string; label: string; description: string; route: string }[];
}

export async function askGuide(body: GuideRequest, signal?: AbortSignal): Promise<GuideStep> {
  return postJson<GuideStep>("/api/assistant/guide", body, signal);
}
