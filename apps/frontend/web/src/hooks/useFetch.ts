// Data-fetch hook (Antho's demo signature): GET a path, expose {data, loading, error, refetch},
// with optional background polling. Once a path has data, background refreshes keep showing the
// stale data instead of flipping the panel back to "Loading" on every poll.

import { useCallback, useEffect, useRef, useState } from "react";

import { getJson } from "../api";

export interface FetchState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useFetch<T>(path: string, refreshMs = 0): FetchState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const hasDataRef = useRef(false);

  const load = useCallback(async () => {
    if (!hasDataRef.current) setLoading(true);
    try {
      const payload = await getJson<T>(path);
      hasDataRef.current = true;
      setData(payload);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    hasDataRef.current = false;
    void load();
    if (refreshMs <= 0) return;
    const timer = window.setInterval(() => void load(), refreshMs);
    return () => window.clearInterval(timer);
  }, [load, refreshMs]);

  return { data, loading, error, refetch: load };
}
