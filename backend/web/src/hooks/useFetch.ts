import { useEffect, useState } from "react";

import { getJson } from "../api";

export interface FetchState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

// Load JSON from a path, re-running when the path (or an explicit nonce) changes.
// Returns the three states the pages render: loading, error, and data.
export function useFetch<T>(path: string, nonce = 0): FetchState<T> {
  const [state, setState] = useState<FetchState<T>>({
    data: null,
    error: null,
    loading: true,
  });

  useEffect(() => {
    let cancelled = false;
    setState({ data: null, error: null, loading: true });
    getJson<T>(path)
      .then((data) => {
        if (!cancelled) setState({ data, error: null, loading: false });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : String(err);
          setState({ data: null, error: message, loading: false });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [path, nonce]);

  return state;
}
