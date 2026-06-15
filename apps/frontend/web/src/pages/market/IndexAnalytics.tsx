// The INDEX's volatility analytics, full width below the constituents row: the 3D surface, the
// per-maturity dollar-Greeks TRANSPOSE table (Greeks as columns, deltas as rows, one maturity in
// view via a selector — owner directive 2026-06-15), then the per-maturity smile accordion. The
// option chain is captured at the index level (SX5E/SPX), so this is index-keyed — not the
// selected constituent. The 3D nappe, smile, and Greeks all clean degenerate (railed/non-finite/
// duplicate) points at the RENDER layer only (lib/volRobust) — the served values are never
// mutated; the backend flag-not-reject policy is honoured, just not plotted as garbage.

import type { AnalyticsResponse, PriceHistoryResponse } from "../../api";
import { AsyncBlock } from "../../components/AsyncBlock";
import { GreeksTermStructure, PriceChart, VolSurface } from "../../components/charts";
import { DollarGreeksByMaturity } from "../../components/DollarGreeksByMaturity";
import { MaturityAccordion } from "../../components/MaturityAccordion";
import { useFetch } from "../../hooks/useFetch";

export function IndexAnalytics({
  underlying,
  asOf,
  currency = "$",
}: {
  underlying: string;
  asOf: string;
  // The index's quote-currency symbol (€ for SX5E), so the monetized Greeks below render in the
  // right currency rather than a hard-coded "$" (05-math-notes). Defaults to "$".
  currency?: string;
}) {
  const analytics = useFetch<AnalyticsResponse>(
    `/api/analytics?underlying=${encodeURIComponent(underlying)}&trade_date=${encodeURIComponent(asOf)}`,
  );
  return (
    <AsyncBlock loading={analytics.loading} error={analytics.error}>
      {analytics.data && (
        <>
          <VolSurface surface={analytics.data.surface} maturities={analytics.data.maturities} />
          {/* The dollar-Greeks term structure (the robust curve view): railed-slice points are
              excluded so a single 108%/140% IV no longer spikes the panel and flattens the rest. */}
          <GreeksTermStructure maturities={analytics.data.maturities} currency={currency} />
          {/* The Greeks block is the per-maturity TRANSPOSE table (Greeks as columns, deltas as
              rows, one maturity in view via the selector) — it replaces the wide overflowing
              strike table / per-band matrix (owner directive 2026-06-15). */}
          <DollarGreeksByMaturity maturities={analytics.data.maturities} currency={currency} />
          {/* The per-maturity SMILE accordion stays (the 2D vol cut); its own dollar-Greeks matrix
              is dropped — the transpose above is the single Greeks readout now. */}
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
