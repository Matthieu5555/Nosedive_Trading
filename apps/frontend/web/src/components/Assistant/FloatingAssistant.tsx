import "./assistant.css";

import { useCallback, useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";

import { ApiError } from "../../api";
import { tourCatalog } from "../../lib/tour/registry";
import { askGuide, type GuideStep } from "./assistantApi";
import { useAssistantFrame } from "./AssistantContext";
import { AssistantPanel, type TourView } from "./AssistantPanel";
import { Spotlight } from "./Spotlight";

interface TourState {
  active: boolean;
  goal: string;
  step: GuideStep | null;
  completed: string[];
}

const IDLE: TourState = { active: false, goal: "", step: null, completed: [] };

// The guide loop. It lives here, one level above the panel, for two reasons the contract calls out:
// FloatingAssistant sits inside the BrowserRouter (so it can read the live route with useLocation),
// and its Spotlight is mounted as a sibling of the panel inside the dock, so the highlight ring keeps
// pointing at the on-screen element even when the user collapses the panel to see the page. The panel
// receives a small TourView and drives the loop through start / next / stop callbacks.
function useGuideTour(): TourView & { spotlightId: string | null } {
  const location = useLocation();
  const [tour, setTour] = useState<TourState>(IDLE);
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // The route at the moment the current "navigate" step was issued. When the path moves away from it
  // the navigate step is satisfied, regardless of which page the user landed on.
  const navFromRef = useRef<string | null>(null);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    navFromRef.current = null;
    setThinking(false);
    setError(null);
    setTour(IDLE);
  }, []);

  // One request for the next step. `goal` and `completed` are passed explicitly so callers can drive
  // the loop from the freshly-computed state rather than the (stale) closed-over state.
  const requestStep = useCallback(
    async (goal: string, completed: string[]) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setError(null);
      setThinking(true);
      try {
        const step = await askGuide(
          { goal, route: location.pathname, completed, catalog: tourCatalog() },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        navFromRef.current = step.expect === "navigate" ? location.pathname : null;
        setTour({ active: !step.done, goal, step, completed });
      } catch (err) {
        if (controller.signal.aborted) return;
        // Surface the failure with the panel's existing error styling and end the tour gracefully, so
        // the Spotlight is never left hanging over a dead loop.
        const detail = err instanceof ApiError ? err.detail : "The guide is unavailable.";
        setError(`Assistant unavailable, ${detail}`);
        navFromRef.current = null;
        setTour(IDLE);
        setThinking(false);
      } finally {
        if (!controller.signal.aborted) setThinking(false);
      }
    },
    [location.pathname],
  );

  const start = useCallback(
    (goal: string) => {
      const trimmed = goal.trim();
      if (!trimmed) return;
      setTour({ active: true, goal: trimmed, step: null, completed: [] });
      void requestStep(trimmed, []);
    },
    [requestStep],
  );

  // Advance: mark the current highlight done and ask for the next step. The manual "Next" button, a
  // matched "click", and a matched "navigate" all funnel through here.
  const advance = useCallback(() => {
    setTour((prev) => {
      if (!prev.active) return prev;
      const id = prev.step?.highlight;
      const completed =
        id && !prev.completed.includes(id) ? [...prev.completed, id] : prev.completed;
      void requestStep(prev.goal, completed);
      return { ...prev, completed };
    });
  }, [requestStep]);

  // expect:"navigate" — advance once the path leaves the page the step was issued on.
  useEffect(() => {
    if (!tour.active || tour.step?.expect !== "navigate") return;
    if (navFromRef.current !== null && location.pathname !== navFromRef.current) {
      navFromRef.current = null;
      advance();
    }
  }, [location.pathname, tour.active, tour.step, advance]);

  // expect:"click" — one-shot listener on the highlighted node. Guard for a missing node (the anchor
  // may not be mounted on this page yet); the manual Next still advances it.
  useEffect(() => {
    if (!tour.active || tour.step?.expect !== "click") return;
    const id = tour.step.highlight;
    if (!id) return;
    const node = document.querySelector<HTMLElement>(`[data-tour-id="${id}"]`);
    if (!node) return;
    const onClick = () => advance();
    node.addEventListener("click", onClick, { once: true });
    return () => node.removeEventListener("click", onClick);
  }, [tour.active, tour.step, advance]);

  // On unmount, abort any in-flight guide request so the Spotlight never outlives the loop.
  useEffect(() => () => abortRef.current?.abort(), []);

  const spotlightId = tour.active && tour.step ? tour.step.highlight : null;

  return {
    active: tour.active,
    thinking,
    step: tour.step,
    error,
    start,
    next: advance,
    stop,
    spotlightId,
  };
}

// The globally-mounted floating assistant. It lives once in the app shell (outside <Routes>) so it
// rides along across every page, fixed to the bottom-right corner. It pulls the active surface frame
// from context: on Market that frame is fully wired, everywhere else it's empty and the panel shows
// its honest "Choose an index and a close" empty state. The launcher and the open panel are both
// positioned by the .assistant-dock wrapper; the panel keeps all of its own behavior (what-am-I-
// looking-at, what's-this, citations, thinking, errors) untouched, and gains the guide loop driven
// from here. The Spotlight is a dock sibling, not a panel child, so its highlight ring keeps pointing
// at the on-screen element even when the user collapses the panel to look at the page.
export function FloatingAssistant() {
  const frame = useAssistantFrame();
  const { spotlightId, ...tour } = useGuideTour();
  return (
    <div className="assistant-dock">
      <AssistantPanel
        underlying={frame.underlying}
        asOf={frame.asOf}
        runId={frame.runId}
        mode={frame.mode}
        focusedElementId={frame.focusedElementId}
        tour={tour}
      />
      <Spotlight tourId={spotlightId} />
    </div>
  );
}
