// The whole-basket price-history preload, owned as one module: a session-scoped promise cache
// plus the hook that reads it. The scan costs ~1 min on the live store and the server does NOT
// stop it on navigation away — so re-firing it on every return to the Market page stacked
// concurrent scans until the disk saturated. Caching the promise means a remount while the first
// preload is still in flight reuses it; a failed preload is evicted so the next mount retries.

import { useEffect, useMemo, useState } from "react";

import type { PriceHistoryBatchResponse } from "../../api";
import { postJson } from "../../api";

const constituentHistoryBatchCache = new Map<string, Promise<PriceHistoryBatchResponse>>();

export function resetConstituentHistoryBatchCacheForTests(): void {
  constituentHistoryBatchCache.clear();
}

function fetchConstituentHistoryBatch(
  symbols: string[],
  asOf: string,
): Promise<PriceHistoryBatchResponse> {
  const key = JSON.stringify([asOf, ...symbols]);
  let promise = constituentHistoryBatchCache.get(key);
  if (promise === undefined) {
    promise = postJson<PriceHistoryBatchResponse>("/api/price-history/batch", {
      underlyings: symbols,
      end: asOf,
    });
    promise.catch(() => constituentHistoryBatchCache.delete(key));
    constituentHistoryBatchCache.set(key, promise);
  }
  return promise;
}

export function useConstituentHistoryBatch(
  symbols: string[],
  asOf: string,
): {
  data: PriceHistoryBatchResponse | null;
  loading: boolean;
  error: string | null;
} {
  const symbolsKey = useMemo(() => JSON.stringify(symbols), [symbols]);
  const [data, setData] = useState<PriceHistoryBatchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (symbolsKey === "[]") {
      setData(null);
      setError(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    void fetchConstituentHistoryBatch(JSON.parse(symbolsKey) as string[], asOf)
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((err) => {
        if (!cancelled) {
          setData(null);
          setError(err instanceof Error ? err.message : String(err));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [symbolsKey, asOf]);

  return { data, loading, error };
}
