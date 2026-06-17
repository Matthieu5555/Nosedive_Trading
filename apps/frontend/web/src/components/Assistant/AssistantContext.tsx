import { createContext, type ReactNode, useContext, useMemo, useState } from "react";

import type { AssistantMode } from "./assistantApi";

// The current surface frame the floating assistant grounds on. Only the page that actually has a
// resolved subject/close (Market today) fills it in; everywhere else it stays empty and the panel
// shows its honest "Choose an index and a close" empty state. focusedElementId travels here too so
// the "What's this?" shortcut can target whatever element the active page has surfaced.
export interface AssistantFrameContext {
  underlying: string;
  asOf: string | null;
  runId: string | null;
  mode: AssistantMode;
  focusedElementId: string | null;
}

export const EMPTY_FRAME: AssistantFrameContext = {
  underlying: "",
  asOf: null,
  runId: null,
  mode: "strict",
  focusedElementId: null,
};

interface AssistantContextValue {
  frame: AssistantFrameContext;
  setFrame: (frame: AssistantFrameContext) => void;
}

const AssistantContext = createContext<AssistantContextValue | null>(null);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [frame, setFrame] = useState<AssistantFrameContext>(EMPTY_FRAME);
  const value = useMemo(() => ({ frame, setFrame }), [frame]);
  return <AssistantContext.Provider value={value}>{children}</AssistantContext.Provider>;
}

// Reading the frame is safe outside a provider (it degrades to the empty frame), so a stray render
// in a test or a storybook never throws; setting it is the part that needs the provider.
export function useAssistantFrame(): AssistantFrameContext {
  return useContext(AssistantContext)?.frame ?? EMPTY_FRAME;
}

export function useSetAssistantFrame(): (frame: AssistantFrameContext) => void {
  const ctx = useContext(AssistantContext);
  return ctx?.setFrame ?? (() => {});
}
