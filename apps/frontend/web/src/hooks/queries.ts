// The canonical data-fetching hooks for the console (ADR 0030, phase-3 hardening). These are
// thin, typed wrappers over the BFF client in src/api.ts using TanStack Query's useQuery — the
// page never touches fetch or even getJson directly, it asks for a domain thing by name and gets
// back {data, isPending, isError, error, refetch, …}. New pages SHOULD reach for hooks here
// rather than the legacy hand-rolled useFetch; existing pages migrate incrementally.
//
// ── Query-key convention ────────────────────────────────────────────────────────────────────
// A key is a tuple, broadest-scope-first, so React Query's prefix matching can invalidate a whole
// family at once. The shape is:
//
//   [domain, resource, ...params]
//
//   ["risk", "portfolios"]                 — the configured-portfolio list (no params)
//   ["risk", "scenarios", portfolioId]     — the persisted stress surface for one selection
//                                            (portfolioId is "" for the all-portfolios view)
//
// Keep params in a stable order and only include what actually scopes the request, so two call
// sites asking for the same data share one cache entry. queryFn forwards React Query's AbortSignal
// to api.ts, so an unmount / key change cancels the in-flight fetch exactly as useFetch did.

import { useQuery } from "@tanstack/react-query";

import { getJson } from "../api";
import type { ScenariosResponse } from "../stressApi";

export interface PortfoliosResponse {
  portfolios: string[];
}

// The configured-portfolio list that drives the Risk Scenarios selector.
export function usePortfolios() {
  return useQuery({
    queryKey: ["risk", "portfolios"] as const,
    queryFn: ({ signal }) => getJson<PortfoliosResponse>("/api/risk/portfolios", signal),
  });
}

// The persisted cron-written stress surface for a selection. An empty `portfolioId` means the
// all-portfolios view; it still scopes the cache key so switching selections does not show stale
// data for the wrong portfolio.
export function useRiskScenarios(portfolioId: string) {
  const query = portfolioId ? `?portfolio_id=${encodeURIComponent(portfolioId)}` : "";
  return useQuery({
    queryKey: ["risk", "scenarios", portfolioId] as const,
    queryFn: ({ signal }) => getJson<ScenariosResponse>(`/api/risk/scenarios${query}`, signal),
  });
}
