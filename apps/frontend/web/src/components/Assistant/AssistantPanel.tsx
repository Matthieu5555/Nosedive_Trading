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
  // the "C'est quoi, ça ?" shortcut asks the assistant about exactly that element via the copy map.
  focusedElementId?: string | null;
}

type Turn = { kind: "question"; text: string } | { kind: "answer"; response: AssistantResponse };

function frameCaption(frame: AssistantFrame): string {
  const parts = [frame.underlying];
  if (frame.close_instant) parts.push(`clôture ${frame.close_instant}`);
  parts.push(frame.mode === "indicative" ? "INDICATIF" : "strict");
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
        const detail = err instanceof ApiError ? err.detail : "L'assistant est indisponible.";
        setError(`Assistant indisponible — ${detail}`);
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
    void ask(`C'est quoi, ${subject} ?`, id);
  }

  if (!open) {
    return (
      <button
        type="button"
        className="assistant-launch"
        aria-expanded={false}
        onClick={() => setOpen(true)}
      >
        Demander à l'assistant
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
          aria-label="Fermer l'assistant"
          onClick={() => setOpen(false)}
        >
          ×
        </button>
      </div>

      {!ready ? (
        <p className="assistant-empty" role="status">
          Choisissez un indice et une clôture pour interroger l'écran.
        </p>
      ) : (
        <>
          <div className="assistant-actions">
            <button
              type="button"
              className="assistant-action"
              onClick={() => void ask("Qu'est-ce que je regarde ?")}
            >
              Qu'est-ce que je regarde ?
            </button>
            <button
              type="button"
              className="assistant-action"
              disabled={!focusedEntry}
              title={
                focusedEntry
                  ? `Expliquer : ${focusedEntry.label}`
                  : "Survolez un élément de l'écran pour l'expliquer"
              }
              onClick={onWhatIsThis}
            >
              {focusedEntry ? `C'est quoi : ${focusedEntry.label} ?` : "C'est quoi, ça ?"}
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
                L'assistant réfléchit…
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
              Votre question
            </label>
            <input
              id="assistant-question"
              className="assistant-form__input"
              value={question}
              placeholder="ex. comment voir pourquoi des lignes sont exclues ?"
              onChange={(event) => setQuestion(event.target.value)}
            />
            <button type="submit" disabled={thinking || question.trim() === ""}>
              {thinking ? "…" : "Envoyer"}
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
