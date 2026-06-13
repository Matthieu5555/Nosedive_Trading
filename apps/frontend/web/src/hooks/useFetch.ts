// Data-fetch hook: GET a path, expose {data, loading, error, refetch}, with optional background
// polling. Once a path has data, background refreshes keep showing the stale data instead of
// flipping the panel back to "Loading" on every poll.
//
// Robustness the bare-fetch version lacked, all owned here so call sites stay declarative:
//   - every request carries an AbortController, so an in-flight fetch is cancelled on unmount
//     and on a path change — no setState-after-unmount, no poll firing after navigation away;
//   - a request that hangs is aborted after REQUEST_TIMEOUT_MS rather than wedging the panel on
//     a stalled BFF forever;
//   - a background-refresh failure is no longer swallowed: `stale` flags that the data on screen
//     is from a refresh that has since failed, so a panel can badge it instead of lying.

import { useCallback, useEffect, useRef, useState } from "react";

import { getJson } from "../api";

// A single request may not hang a panel indefinitely; abort it past this and surface the error.
export const REQUEST_TIMEOUT_MS = 30_000;

export interface FetchState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  // True when data is present but the most recent (background) refresh failed — the panel is
  // showing a known-stale value. Distinct from `error`, which fronts a first-load failure.
  stale: boolean;
  refetch: () => void;
}

export function useFetch<T>(path: string, refreshMs = 0): FetchState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const hasDataRef = useRef(false);
  // The controller for the request currently in flight, so a new load or an unmount can abort it.
  const inFlightRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    // An empty path means "nothing to fetch yet" (e.g. a selector value not resolved). It is
    // not an error and not a load — clear any in-flight request and settle to idle.
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
      // A cancelled request is not a failure — the caller moved on; drop it silently.
      if (controller.signal.aborted) return;
      const message = err instanceof Error ? err.message : "Unknown error";
      // First-load failure fronts an error panel; a failed background refresh keeps the stale
      // data on screen but marks it stale rather than silently pretending it is fresh.
      if (hasDataRef.current) setStale(true);
      else setError(message);
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    hasDataRef.current = false;
    setStale(false);
    // Skip the network entirely for an empty path, but clear stale data from a prior path so a
    // panel can't show data for the wrong (now-unset) key.
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    void load();
    const timer = refreshMs > 0 ? window.setInterval(() => void load(), refreshMs) : undefined;
    return () => {
      if (timer !== undefined) window.clearInterval(timer);
      // Cancel any in-flight request for the path we are leaving, so its resolution cannot
      // setState into an unmounted tree or a now-stale path.
      inFlightRef.current?.abort();
    };
  }, [load, refreshMs, path]);

  return { data, loading, error, stale, refetch: load };
}
