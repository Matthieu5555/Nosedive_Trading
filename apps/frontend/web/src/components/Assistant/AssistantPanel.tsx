import "./assistant.css";

import { type FormEvent, useCallback, useRef, useState } from "react";

import { ApiError } from "../../api";
import { EXPLAIN, explainEntry } from "../../lib/explain";
import {
  askAssistant,
  type AssistantFrame,
  type AssistantMode,
  type AssistantResponse,
} from "./assistantApi";

interface AssistantPanelProps {
  underlying: string;
  asOf: string | null;
  runId?: string | null;
  mode?: AssistantMode;
  // The element the user is hovering / has selected on the page (a chart, a scorecard). When set,
  // the "What's this?" shortcut asks the assistant about exactly that element via the copy map.
  focusedElementId?: string | null;
}

type Turn = { kind: "question"; text: string } | { kind: "answer"; response: AssistantResponse };

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
}: AssistantPanelProps) {
  const [open, setOpen] = useState(false);
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

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    void ask(question);
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
        Ask the assistant
      </button>
    );
  }

  const focusedEntry = focusedElementId ? explainEntry(focusedElementId) : null;

  return (
    <aside className="assistant-panel" aria-label="Assistant">
      <div className="assistant-panel__head">
        <h2>Assistant</h2>
        <button
          type="button"
          className="assistant-panel__close"
          aria-label="Close the assistant"
          onClick={() => setOpen(false)}
        >
          ×
        </button>
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
            {thinking && (
              <p className="assistant-thinking" role="status" aria-live="polite" aria-busy="true">
                The assistant is thinking…
              </p>
            )}
            {error && (
              <p className="assistant-error state-panel state-panel-error" role="alert">
                {error}
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
