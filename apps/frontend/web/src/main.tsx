import "./index.css";

import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { GlobalErrorBanner } from "./components/GlobalErrorBanner";
import { queryClient } from "./lib/queryClient";
import { installGlobalErrorListeners } from "./lib/runtimeErrors";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("missing #root element");
}

// Arm the window-level catch-alls before the tree mounts, so an error during the very first render
// already lands on the banner rather than dying silently in the console.
installGlobalErrorListeners();

createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* The always-on failure surface: sits above the shell so any escaped error is visible on
          every route. */}
      <GlobalErrorBanner />
      {/* A root boundary so a crash in the shell itself (topbar/nav, before the per-route
          boundaries) degrades to a labelled tile instead of a blank white screen. */}
      <ErrorBoundary label="Application">
        <App />
      </ErrorBoundary>
      {/* Dev-only: tree-shaken out of the production bundle, never mounted for operators. */}
      {import.meta.env.DEV && <ReactQueryDevtools initialIsOpen={false} />}
    </QueryClientProvider>
  </StrictMode>,
);
