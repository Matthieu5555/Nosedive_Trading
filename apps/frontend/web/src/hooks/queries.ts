import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  type AttributionResponse,
  fetchAttribution,
  fetchReconciliation,
  getJson,
  type HealthResponse,
  type IbkrStatus,
  type Job,
  postJson,
  type ProvidersResponse,
  type ReconciliationResponse,
  type RecordedDatesResponse,
  type RunRequest,
} from "../api";
import type { ScenariosResponse } from "../stressApi";

export interface PortfoliosResponse {
  portfolios: string[];
}

export interface RunUnderlyingsResponse {
  underlyings: string[];
}

export interface JobsResponse {
  jobs: Job[];
}

const HEALTH_REFRESH_MS = 30_000;
const JOBS_REFRESH_MS = 4_000;
const IBKR_STATUS_REFRESH_MS = 20_000;

export function useHealth() {
  return useQuery({
    queryKey: ["operations", "health"] as const,
    queryFn: ({ signal }) => getJson<HealthResponse>("/api/health", signal),
    refetchInterval: HEALTH_REFRESH_MS,
  });
}

export function useProviders() {
  return useQuery({
    queryKey: ["operations", "providers"] as const,
    queryFn: ({ signal }) => getJson<ProvidersResponse>("/api/providers", signal),
  });
}

export function useRunUnderlyings() {
  return useQuery({
    queryKey: ["operations", "run-underlyings"] as const,
    queryFn: ({ signal }) => getJson<RunUnderlyingsResponse>("/api/run/underlyings", signal),
  });
}

export function useJobs() {
  return useQuery({
    queryKey: ["operations", "jobs"] as const,
    queryFn: ({ signal }) => getJson<JobsResponse>("/api/jobs", signal),
    refetchInterval: (query) => {
      const jobs = query.state.data?.jobs ?? [];
      return jobs.some((job) => job.state === "queued" || job.state === "running")
        ? JOBS_REFRESH_MS
        : false;
    },
  });
}

export function useRecordedDates(index: string) {
  const query = index ? `?index=${encodeURIComponent(index)}` : "";
  return useQuery({
    queryKey: ["operations", "recorded-dates", index] as const,
    queryFn: ({ signal }) => getJson<RecordedDatesResponse>(`/api/recorded-dates${query}`, signal),
  });
}

export function useLaunchRun() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: RunRequest) => postJson<Job>("/api/run", body),
    onSuccess: (job) => {
      client.setQueryData<JobsResponse>(["operations", "jobs"], (prev) => ({
        jobs: [job, ...(prev?.jobs ?? []).filter((existing) => existing.job_id !== job.job_id)],
      }));
      void client.invalidateQueries({ queryKey: ["operations", "jobs"] });
    },
  });
}

const IBKR_STATUS_KEY = ["operations", "ibkr-status"] as const;

export function useIbkrStatus() {
  return useQuery({
    queryKey: IBKR_STATUS_KEY,
    queryFn: ({ signal }) => getJson<IbkrStatus>("/api/ibkr/status", signal),
    refetchInterval: IBKR_STATUS_REFRESH_MS,
  });
}

// Open the brokerage session (ssodh/init) when already authenticated at the SSO layer. A not-ready
// path comes back as a 409 ApiError the panel surfaces verbatim (with the scripts/ibkr_login.py
// hint); a success returns the fresh status, which we also push into the status query cache so the
// pill updates immediately without waiting for the next poll.
export function useIbkrConnect() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => postJson<IbkrStatus>("/api/ibkr/connect", {}),
    onSuccess: (status) => {
      client.setQueryData<IbkrStatus>(IBKR_STATUS_KEY, status);
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: IBKR_STATUS_KEY });
    },
  });
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

export function useBookAttribution(portfolioId: string) {
  return useQuery<AttributionResponse>({
    queryKey: ["attribution", "book", portfolioId] as const,
    queryFn: ({ signal }) =>
      fetchAttribution({ level: "book", portfolioId: portfolioId || undefined }, signal),
  });
}

export function useReconciliation(accountId: string) {
  return useQuery<ReconciliationResponse>({
    queryKey: ["reconciliation", accountId] as const,
    queryFn: ({ signal }) => fetchReconciliation(accountId || undefined, signal),
  });
}
