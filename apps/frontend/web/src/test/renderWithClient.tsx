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
