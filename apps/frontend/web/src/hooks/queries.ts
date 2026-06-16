import { useQuery } from "@tanstack/react-query";

import { getJson } from "../api";
import type { ScenariosResponse } from "../stressApi";

export interface PortfoliosResponse {
  portfolios: string[];
}

export function usePortfolios() {
  return useQuery({
    queryKey: ["risk", "portfolios"] as const,
    queryFn: ({ signal }) => getJson<PortfoliosResponse>("/api/risk/portfolios", signal),
  });
}

export function useRiskScenarios(portfolioId: string) {
  const query = portfolioId ? `?portfolio_id=${encodeURIComponent(portfolioId)}` : "";
  return useQuery({
    queryKey: ["risk", "scenarios", portfolioId] as const,
    queryFn: ({ signal }) => getJson<ScenariosResponse>(`/api/risk/scenarios${query}`, signal),
  });
}
