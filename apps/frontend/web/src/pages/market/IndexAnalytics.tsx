// The INDEX's volatility analytics, full width below the constituents row: the 3D surface, the
// dollar-Greeks term structure (the curve view), then the per-maturity smile accordion (each
// maturity carries its dollar Greeks in decimal AND currency). The option chain is captured at
// the index level (SX5E/SPX), so this is index-keyed — not the selected constituent.

import type { AnalyticsResponse, PriceHistoryResponse } from "../../api";
import { AsyncBlock } from "../../components/AsyncBlock";
import { GreeksTermStructure, PriceChart, VolSurface } from "../../components/charts";
import { MaturityAccordion } from "../../components/MaturityAccordion";
import { useFetch } from "../../hooks/useFetch";

export function IndexAnalytics({ underlying, asOf }: { underlying: string; asOf: string }) {
  const analytics = useFetch<AnalyticsResponse>(
    `/api/analytics?underlying=${encodeURIComponent(underlying)}&trade_date=${encodeURIComponent(asOf)}`,
  );
  return (
    <AsyncBlock loading={analytics.loading} error={analytics.error}>
      {analytics.data && (
        <>
          <VolSurface maturities={analytics.data.maturities} />
          <GreeksTermStructure maturities={analytics.data.maturities} />
          <MaturityAccordion maturities={analytics.data.maturities} />
        </>
      )}
    </AsyncBlock>
  );
}

// The index's own daily OHLC history, which leads the page (price-first).
export function IndexHistory({ underlying, asOf }: { underlying: string; asOf: string }) {
  const price = useFetch<PriceHistoryResponse>(
    `/api/price-history?underlying=${encodeURIComponent(underlying)}&end=${encodeURIComponent(asOf)}`,
  );
  return (
    <AsyncBlock loading={price.loading} error={price.error}>
      {price.data && <PriceChart data={price.data} />}
    </AsyncBlock>
  );
}
