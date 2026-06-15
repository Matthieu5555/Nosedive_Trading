// The single TanStack Query client for the operator console (ADR 0030). Defaults are tuned for
// a console watching live-ish BFF data rather than a content site:
//
//   - staleTime 30s: the BFF's analytics/risk payloads are cron-/snapshot-backed, not per-second
//     ticks, so a half-minute of treating a result as fresh kills redundant refetches when an
//     operator flips between tabs, without serving genuinely old numbers.
//   - retry 2 (bounded): a flaky BFF hiccup is retried a couple of times; a real outage surfaces
//     as an error panel quickly instead of hammering a down service or hanging the panel.
//   - refetchOnWindowFocus off: an operator alt-tabbing to a chat window should not trigger a
//     refetch storm across every mounted panel; explicit refetch / interval polling drives refresh.
//
// This module imports nothing internal (only the external library), so it sits in the `lib` layer
// and any layer above may construct/consume it. The provider that hands it to React lives in the
// app shell (src/main.tsx).

import { QueryClient } from "@tanstack/react-query";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 2,
      refetchOnWindowFocus: false,
    },
  },
});
