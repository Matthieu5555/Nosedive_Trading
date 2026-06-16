import { useCallback, useEffect, useRef, useState } from "react";

import { getJson } from "../api";

export const REQUEST_TIMEOUT_MS = 30_000;

export interface FetchState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;

  stale: boolean;
  refetch: () => void;
}

export function useFetch<T>(path: string, refreshMs = 0): FetchState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const hasDataRef = useRef(false);

  const inFlightRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    if (!path) {
      inFlightRef.current?.abort();
      setLoading(false);
      return;
    }
    inFlightRef.current?.abort();
    const controller = new AbortController();
    inFlightRef.current = controller;
    if (!hasDataRef.current) setLoading(true);
    try {
      const payload = await getJson<T>(path, controller.signal);
      if (controller.signal.aborted) return;
      hasDataRef.current = true;
      setData(payload);
      setError(null);
      setStale(false);
    } catch (err) {
      if (controller.signal.aborted) return;
      const message = err instanceof Error ? err.message : "Unknown error";

      if (hasDataRef.current) setStale(true);
      else setError(message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    hasDataRef.current = false;
    setStale(false);

    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    void load();
    const timer = refreshMs > 0 ? window.setInterval(() => void load(), refreshMs) : undefined;
    return () => {
      if (timer !== undefined) window.clearInterval(timer);

      inFlightRef.current?.abort();
    };
  }, [load, refreshMs, path]);

  return { data, loading, error, stale, refetch: load };
}
