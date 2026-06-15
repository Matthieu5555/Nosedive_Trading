// Render a component under a FRESH TanStack Query client per call (M10 + phase-3). A page that
// uses the new useQuery-based hooks needs a QueryClientProvider in the tree; this wraps
// @testing-library's render with one, so component tests stay as terse as a bare render().
//
// Two things matter for test isolation:
//   - retry: false — an error test must surface the failure immediately, not hang while React
//     Query burns through the production retry budget (which would blow the test timeout).
//   - a NEW QueryClient every call — no cache bleeds from one test's success into the next test's
//     "should be loading/empty" assertion. cleanup() in setup.ts unmounts the tree; the client is
//     simply discarded with it.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";

export function makeTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

export function renderWithClient(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
): RenderResult & { queryClient: QueryClient } {
  const queryClient = makeTestQueryClient();
  const result = render(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
    ...options,
  });
  return { ...result, queryClient };
}
