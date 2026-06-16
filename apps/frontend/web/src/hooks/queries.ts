import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  getJson,
  type HealthResponse,
  type Job,
  postJson,
  type ProvidersResponse,
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
