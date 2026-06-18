import "./assistant.css";

import { type FormEvent, useCallback, useRef, useState } from "react";

import { ApiError } from "../../api";
import { EXPLAIN, explainEntry } from "../../lib/explain";
import type { GuideStep } from "./assistantApi";
import {
  askAssistant,
  type AssistantFrame,
  type AssistantMode,
  type AssistantResponse,
} from "./assistantApi";

// The slice of the guide loop the panel renders and drives. The loop itself (route watching,
// askGuide, click listeners, Spotlight mount) lives in FloatingAssistant, which has the router and a
// mount point that survives a panel collapse. The panel only reads this view and calls back.
export interface TourView {
  active: boolean;
  thinking: boolean;
  step: GuideStep | null;
  error: string | null;
  start: (goal: string) => void;
  next: () => void;
  stop: () => void;
}

interface AssistantPanelProps {
  underlying: string;
  asOf: string | null;
  runId?: string | null;
  mode?: AssistantMode;
  // The element the user is hovering / has selected on the page (a chart, a scorecard). When set,
  // the "What's this?" shortcut asks the assistant about exactly that element via the copy map.
  focusedElementId?: string | null;
  // The guide loop lives one level up in FloatingAssistant (it needs the router's useLocation, and
  // its Spotlight must survive a panel collapse). The panel only renders the loop's view and calls
  // back to start / advance / stop it, so the panel stays router-free for its direct-render tests.
  // When tour is undefined (no provider, as in the legacy unit tests), the guide affordances simply
  // don't render and every existing behavior is untouched.
  tour?: TourView;
}

type Turn = { kind: "question"; text: string } | { kind: "answer"; response: AssistantResponse };

// "how do i ..." anywhere in the text (case-insensitive) reads as a guide intent rather than a
// grounded question. Kept deliberately loose, the manual "Show me how" entry point covers the rest.
function isGuideIntent(text: string): boolean {
  return /how do i/i.test(text);
}

function frameCaption(frame: AssistantFrame): string {
  const parts = [frame.underlying];
  // The close instant is the venue time-of-day + zone ("17:30 CEST"); the date travels separately on
  // the frame, so the caption pairs them ("close 2026-06-17 17:30 CEST") — the same as-of phrasing
  // the surface caption uses, never a bare time that can't say which day.
  if (frame.close_instant) {
    parts.push(`close ${frame.trade_date} ${frame.close_instant}`);
  }
  parts.push(frame.mode === "indicative" ? "INDICATIVE" : "strict");
  if (frame.coverage_label) parts.push(frame.coverage_label);
  return parts.join(" · ");
}

function AnswerTurn({ response }: { response: AssistantResponse }) {
  const honestGap = !response.grounded;
  return (
    <div className={honestGap ? "assistant-answer assistant-answer--gap" : "assistant-answer"}>
      <p
        className="assistant-answer__text"
        role={honestGap ? "status" : undefined}
        aria-live={honestGap ? "polite" : undefined}
      >
        {response.answer}
      </p>
      {response.citations.length > 0 && (
        <ul className="assistant-citations" aria-label="Citations">
          {response.citations.map((cite) => {
            const entry = explainEntry(cite.id);
            return (
              <li key={`${cite.id}:${cite.value}`} className="assistant-citation">
                <span className="assistant-citation__label">{entry?.label ?? cite.label}</span>
                <span className="assistant-citation__value">{cite.value}</span>
                <span className="assistant-citation__source">{cite.source}</span>
              </li>
            );
          })}
        </ul>
      )}
      <p className="assistant-answer__frame status">{frameCaption(response.frame)}</p>
    </div>
  );
}

export function AssistantPanel({
  underlying,
  asOf,
  runId,
  mode = "strict",
  focusedElementId,
  tour,
}: AssistantPanelProps) {
  const [open, setOpen] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const ready = underlying !== "" && asOf !== null && asOf !== "";

  const ask = useCallback(
    async (text: string, elementId?: string | null) => {
      const trimmed = text.trim();
      if (!trimmed || !ready || asOf === null) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setTurns((prev) => [...prev, { kind: "question", text: trimmed }]);
      setQuestion("");
      setError(null);
      setThinking(true);
      try {
        const response = await askAssistant(
          {
            question: trimmed,
            underlying,
            trade_date: asOf,
            run_id: runId ?? null,
            mode,
            element_id: elementId ?? null,
          },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setTurns((prev) => [...prev, { kind: "answer", response }]);
      } catch (err) {
        if (controller.signal.aborted) return;
        const detail = err instanceof ApiError ? err.detail : "The assistant is unavailable.";
        setError(`Assistant unavailable, ${detail}`);
      } finally {
        if (!controller.signal.aborted) setThinking(false);
      }
    },
    [ready, asOf, underlying, runId, mode],
  );

  // A guide intent ("how do i ...") starts a tour instead of asking a grounded question. The goal is
  // echoed into the thread as the user's question so the conversation reads naturally, then the loop
  // (owned by FloatingAssistant) drives the steps. With no tour provider the input falls back to a
  // normal ask, so the panel still works standalone.
  const startGuideOrAsk = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (!trimmed) return;
      if (tour && isGuideIntent(trimmed)) {
        setTurns((prev) => [...prev, { kind: "question", text: trimmed }]);
        setQuestion("");
        setError(null);
        void tour.start(trimmed);
        return;
      }
      void ask(trimmed);
    },
    [tour, ask],
  );

  const refresh = useCallback(() => {
    abortRef.current?.abort();
    setTurns([]);
    setError(null);
    setThinking(false);
    tour?.stop();
  }, [tour]);

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    startGuideOrAsk(question);
  }

  function onWhatIsThis() {
    const id = focusedElementId;
    if (!id) return;
    const entry = explainEntry(id);
    const subject = entry ? entry.label : id;
    void ask(`What is ${subject}?`, id);
  }

  if (!open) {
    return (
      <button
        type="button"
        className="assistant-launch"
        aria-expanded={false}
        onClick={() => setOpen(true)}
      >
        <svg
          className="assistant-launch__spark"
          viewBox="0 0 24 24"
          width="14"
          height="14"
          aria-hidden="true"
          focusable="false"
        >
          {/* A four-point sparkle: the AI signal, carried by the glyph (accent + shimmer), not the pill. */}
          <path
            d="M12 2c.4 3.6 1.8 5 5.4 5.4-3.6.4-5 1.8-5.4 5.4-.4-3.6-1.8-5-5.4-5.4C10.2 7 11.6 5.6 12 2Z"
            fill="currentColor"
          />
          <path
            d="M18.5 14c.2 1.7.9 2.4 2.6 2.6-1.7.2-2.4.9-2.6 2.6-.2-1.7-.9-2.4-2.6-2.6 1.7-.2 2.4-.9 2.6-2.6Z"
            fill="currentColor"
            opacity="0.7"
          />
        </svg>
        Ask the assistant
      </button>
    );
  }

  const focusedEntry = focusedElementId ? explainEntry(focusedElementId) : null;
  const tourActive = tour?.active ?? false;
  const tourError = tour?.error ?? null;

  return (
    <aside
      className={expanded ? "assistant-panel assistant-panel--expanded" : "assistant-panel"}
      aria-label="Assistant"
    >
      <div className="assistant-panel__head">
        <h2>Assistant</h2>
        <div className="assistant-panel__head-controls">
          <button
            type="button"
            className="assistant-panel__icon"
            aria-label="Clear the conversation"
            title="Clear the conversation"
            onClick={refresh}
          >
            ↺
          </button>
          <button
            type="button"
            className="assistant-panel__icon"
            aria-label={expanded ? "Return to corner" : "Expand the assistant"}
            aria-pressed={expanded}
            title={expanded ? "Return to corner" : "Expand the assistant"}
            onClick={() => setExpanded((v) => !v)}
          >
            {expanded ? "⤡" : "⤢"}
          </button>
          <button
            type="button"
            className="assistant-panel__close"
            aria-label="Close the assistant"
            onClick={() => setOpen(false)}
          >
            ×
          </button>
        </div>
      </div>

      {!ready ? (
        <p className="assistant-empty" role="status">
          Choose an index and a close to query the screen.
        </p>
      ) : (
        <>
          <div className="assistant-actions">
            <button
              type="button"
              className="assistant-action"
              onClick={() => void ask("What am I looking at?")}
            >
              What am I looking at?
            </button>
            <button
              type="button"
              className="assistant-action"
              disabled={!focusedEntry}
              title={
                focusedEntry
                  ? `Explain: ${focusedEntry.label}`
                  : "Hover over an element on the screen to explain it"
              }
              onClick={onWhatIsThis}
            >
              {focusedEntry ? `What is: ${focusedEntry.label}?` : "What's this?"}
            </button>
            {tour && (
              <button
                type="button"
                className="assistant-action"
                disabled={tourActive}
                title="Ask the assistant to walk you through a task"
                onClick={() => startGuideOrAsk(question.trim() || "How do I read this screen?")}
              >
                Show me how
              </button>
            )}
          </div>

          <div className="assistant-thread">
            {turns.map((turn, i) =>
              turn.kind === "question" ? (
                <p key={`q${i}`} className="assistant-question">
                  {turn.text}
                </p>
              ) : (
                <AnswerTurn key={`a${i}`} response={turn.response} />
              ),
            )}

            {tour?.step && (
              <div className="assistant-answer assistant-answer--guide">
                <p className="assistant-answer__text" role="status" aria-live="polite">
                  {tour.step.say}
                </p>
              </div>
            )}
            {tourActive && tour && (
              <div className="assistant-guide-controls">
                <button type="button" className="assistant-action" onClick={() => void tour.next()}>
                  Next
                </button>
                <button type="button" className="assistant-action" onClick={() => tour.stop()}>
                  Stop tour
                </button>
              </div>
            )}

            {(thinking || tour?.thinking) && (
              <p className="assistant-thinking" role="status" aria-live="polite" aria-busy="true">
                The assistant is thinking…
              </p>
            )}
            {(error || tourError) && (
              <p className="assistant-error state-panel state-panel-error" role="alert">
                {error ?? tourError}
              </p>
            )}
          </div>

          <form className="assistant-form" onSubmit={onSubmit}>
            <label className="assistant-form__label" htmlFor="assistant-question">
              Your question
            </label>
            <input
              id="assistant-question"
              className="assistant-form__input"
              value={question}
              placeholder="e.g. how do I see why rows are excluded?"
              onChange={(event) => setQuestion(event.target.value)}
            />
            <button type="submit" disabled={thinking || question.trim() === ""}>
              {thinking ? "…" : "Send"}
            </button>
          </form>
        </>
      )}
    </aside>
  );
}

// Re-exported under the historical name so existing importers keep working; the single source is
// the canonical lib/explain.ts copy map.
export { EXPLAIN as ASSISTANT_COPY };
