import "./assistant.css";

import { useAssistantFrame } from "./AssistantContext";
import { AssistantPanel } from "./AssistantPanel";

// The globally-mounted floating assistant. It lives once in the app shell (outside <Routes>) so it
// rides along across every page, fixed to the bottom-right corner. It pulls the active surface frame
// from context: on Market that frame is fully wired, everywhere else it's empty and the panel shows
// its honest "Choose an index and a close" empty state. The launcher and the open panel are both
// positioned by the .assistant-dock wrapper; the panel keeps all of its own behavior (what-am-I-
// looking-at, what's-this, citations, thinking, errors) untouched.
export function FloatingAssistant() {
  const frame = useAssistantFrame();
  return (
    <div className="assistant-dock">
      <AssistantPanel
        underlying={frame.underlying}
        asOf={frame.asOf}
        runId={frame.runId}
        mode={frame.mode}
        focusedElementId={frame.focusedElementId}
      />
    </div>
  );
}
