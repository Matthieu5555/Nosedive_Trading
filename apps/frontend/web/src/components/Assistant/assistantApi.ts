import { postJson } from "../../api";

export type AssistantMode = "strict" | "indicative";

// The active surface frame the panel posts with every question, so the assistant grounds on the
// SAME (subject · as-of · mode) the page is showing — it can never describe a different frame than
// the chart. The BFF resolves the close instant (17:30 CET for SX5E, OESX settlement) server-side;
// the front never re-derives it, it only renders what the frame carries back.
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
