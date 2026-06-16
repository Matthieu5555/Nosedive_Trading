import type { AnalyticsResponse, PriceHistoryResponse } from "../../api";
import { AsyncBlock } from "../../components/AsyncBlock";
import {
  AtmTermStructure,
  GreeksTermStructure,
  PriceChart,
  VolHeatmap,
  VolSurface,
} from "../../components/charts";
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
          {/* The flat nappe (§3.4): the same dense lattice as the 3D, stacked below it, sharing
              one value→colour scale so a colour means the same IV in both. */}
          <VolHeatmap surface={analytics.data.surface} />
          {/* The dollar-Greeks term structure (the robust curve view): railed-slice points are
              excluded so a single 108%/140% IV no longer spikes the panel and flattens the rest. */}
          <GreeksTermStructure maturities={analytics.data.maturities} currency={currency} />
          {/* The Greeks block is the per-maturity TRANSPOSE table (Greeks as columns, deltas as
              rows, one maturity in view via the selector) — it replaces the wide overflowing
              strike table / per-band matrix (owner directive 2026-06-15). */}
          <DollarGreeksByMaturity maturities={analytics.data.maturities} currency={currency} />
          {/* The §3.5 2D cuts: the ATM term structure (vol vs maturity) beside the per-maturity
              smile accordion. ATM reads off the dense nappe when present, else the smiles. */}
          <AtmTermStructure
            surface={analytics.data.surface}
            maturities={analytics.data.maturities}
          />
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
