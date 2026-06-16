import { useEffect, useMemo, useState } from "react";

import {
  type AnalyticsResponse,
  type ConstituentsResponse,
  type IndicesResponse,
  type PriceHistoryResponse,
  type RecordedDatesResponse,
  type SignalsResponse,
} from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { PriceChart, VolSurface } from "../components/charts";
import { ConstituentTable } from "../components/ConstituentTable";
import { DispersionStrip } from "../components/DispersionStrip";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Scorecards } from "../components/Scorecards";
import { TenorPanel } from "../components/TenorPanel";
import { useFetch } from "../hooks/useFetch";
import { currencySymbol } from "../lib/format";
import { AsOfSelect, QcBadge } from "./market/marketHeader";

export function MarketPage() {
  const indices = useFetch<IndicesResponse>("/api/indices");
  const indexOptions = useMemo(() => indices.data?.indices ?? [], [indices.data]);

  const [index, setIndex] = useState("");
  useEffect(() => {
    if (indexOptions.length === 0) return;
    if (!index || !indexOptions.some((o) => o.symbol === index)) {
      setIndex(indexOptions[0].symbol);
    }
  }, [indexOptions, index]);

  // The chosen as-of fetch. The picker now lists ONE canonical close per trade_date (the newest run,
  // collapsed serving-side in /api/recorded-dates), so a same-day re-fetch shows once, latest wins.
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const recorded = useFetch<RecordedDatesResponse>(
    index ? `/api/recorded-dates?index=${encodeURIComponent(index)}` : "",
  );

  const available = recorded.data?.available ?? [];
  const selectedFetch =
    available.find((fetch) => (fetch.run_id ?? fetch.date) === selectedKey) ?? available[0] ?? null;
  const effectiveRunId = selectedFetch?.run_id ?? null;
  const effectiveAsOf = selectedFetch?.date ?? null;
  const selectedFetchKey = selectedFetch ? (selectedFetch.run_id ?? selectedFetch.date) : null;

  // The page is INDEX-KEYED ONLY (ADR 0051): every analytics/price read is the index itself; the
  // constituent table below is display-only and never routes a member into the surface.
  const analytics = useFetch<AnalyticsResponse>(
    index && effectiveAsOf
      ? `/api/analytics?underlying=${encodeURIComponent(index)}&trade_date=${encodeURIComponent(effectiveAsOf)}` +
          (effectiveRunId ? `&run_id=${encodeURIComponent(effectiveRunId)}` : "")
      : "",
  );
  const price = useFetch<PriceHistoryResponse>(
    index && effectiveAsOf
      ? `/api/price-history?underlying=${encodeURIComponent(index)}&end=${encodeURIComponent(effectiveAsOf)}`
      : "",
  );
  // The persisted signal layer for the index, as-of: RV−IV for the scorecard and ρ̄ for the
  // dispersion strip — both read straight off /api/signals (the BFF computed them; we never recompute).
  const signals = useFetch<SignalsResponse>(
    index && effectiveAsOf
      ? `/api/signals?underlying=${encodeURIComponent(index)}&trade_date=${encodeURIComponent(effectiveAsOf)}`
      : "",
  );
  const constituents = useFetch<ConstituentsResponse>(
    index && effectiveAsOf
      ? `/api/constituents?index=${encodeURIComponent(index)}&as_of=${encodeURIComponent(effectiveAsOf)}`
      : "",
  );
  const constituentList = useMemo(() => constituents.data?.constituents ?? [], [constituents.data]);

  const currency = currencySymbol(indexOptions.find((o) => o.symbol === index)?.currency);

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Form a view on what the market is pricing</p>
          <h1>Market</h1>
        </div>
        <div className="control-row">
          <select
            aria-label="Index"
            value={index}
            disabled={indexOptions.length === 0}
            onChange={(event) => {
              setIndex(event.target.value);
              setSelectedKey(null);
            }}
          >
            {indexOptions.map((item) => (
              <option key={item.symbol} value={item.symbol}>
                {item.name} ({item.symbol})
              </option>
            ))}
          </select>
          <AsOfSelect
            recorded={recorded.data}
            value={selectedFetchKey}
            onChange={(key) => setSelectedKey(key)}
          />
        </div>
      </div>

      {/* The index list gates the whole page: if it fails, nothing below can render, so its error
          must front the page rather than leave a dead screen. Indices error takes precedence; once
          the index resolves, the recorded-dates error fronts here instead. */}
      <AsyncBlock
        loading={indices.loading || recorded.loading}
        error={indices.error ?? recorded.error}
      >
        {recorded.data &&
          (() => {
            if (available.length === 0 || effectiveAsOf === null) {
              return (
                <article className="panel">
                  <p>No capture runs to show for {recorded.data.index} yet.</p>
                </article>
              );
            }
            const qc = selectedFetch?.qc ?? "unknown";
            return (
              <div className="market-scroll">
                <div className="market-scroll__status">
                  <span className="status">
                    {index} · as of {effectiveAsOf} <QcBadge qc={qc} />
                  </span>
                </div>

                {/* 1 — Price (context). */}
                <article className="panel" aria-label={`${index} daily history`}>
                  <div className="panel-heading">
                    <div>
                      <p className="panel-kicker">{index}</p>
                      <h2>Price</h2>
                    </div>
                    <span className="status">daily OHLC</span>
                  </div>
                  <ErrorBoundary label="Price">
                    <AsyncBlock loading={price.loading} error={price.error}>
                      {price.data && <PriceChart data={price.data} />}
                    </AsyncBlock>
                  </ErrorBoundary>
                </article>

                {/* 2 — Scorecards (the instant read). RV−IV from /api/signals; the smile-derived
                    level/skew/convexity from the projected analytics at the reference tenor. */}
                <article className="panel" aria-label="Volatility scorecards">
                  <div className="panel-heading">
                    <div>
                      <p className="panel-kicker">{index}</p>
                      <h2>Scorecards</h2>
                    </div>
                    <span className="status">the instant read</span>
                  </div>
                  <ErrorBoundary label="Scorecards">
                    <AsyncBlock loading={analytics.loading || signals.loading} error={analytics.error}>
                      {analytics.data && (
                        <Scorecards
                          maturities={analytics.data.maturities}
                          ivVsRealized={signals.data?.by_kind?.iv_vs_realized?.[0] ?? null}
                        />
                      )}
                    </AsyncBlock>
                  </ErrorBoundary>
                </article>

                {/* 3 — 3D nappe (the all-maturity gestalt), side-agnostic. */}
                <article className="panel" aria-label="Volatility surface">
                  <div className="panel-heading">
                    <div>
                      <p className="panel-kicker">{index}</p>
                      <h2>Volatility nappe</h2>
                    </div>
                    <span className="status">all maturities</span>
                  </div>
                  <ErrorBoundary label="3D surface">
                    <AsyncBlock loading={analytics.loading} error={analytics.error}>
                      {analytics.data && (
                        <VolSurface
                          surface={analytics.data.surface}
                          maturities={analytics.data.maturities}
                        />
                      )}
                    </AsyncBlock>
                  </ErrorBoundary>
                </article>

                {/* 4 — ONE tenor selector → {smile + greeks table} for that tenor. */}
                <ErrorBoundary label="Tenor view">
                  <AsyncBlock loading={analytics.loading} error={analytics.error}>
                    {analytics.data && (
                      <TenorPanel maturities={analytics.data.maturities} currency={currency} />
                    )}
                  </AsyncBlock>
                </ErrorBoundary>

                {/* 5 — ρ̄ / dispersion (realized-vol implied correlation), the secondary strip. */}
                <article className="panel" aria-label="Dispersion">
                  <div className="panel-heading">
                    <div>
                      <p className="panel-kicker">{index}</p>
                      <h2>Dispersion (ρ̄)</h2>
                    </div>
                    <span className="status">realized-vol diagnostic</span>
                  </div>
                  <ErrorBoundary label="Dispersion">
                    <AsyncBlock loading={signals.loading} error={signals.error}>
                      {signals.data && (
                        <DispersionStrip
                          index={index}
                          signal={signals.data.by_kind?.implied_correlation?.[0] ?? null}
                        />
                      )}
                    </AsyncBlock>
                  </ErrorBoundary>
                </article>

                {/* Secondary: constituents (weight + price), display-only and index-keyed — a member
                    row never routes into the surface (ADR 0051). The per-tenor capture-coverage
                    ratios were dropped here: they are a data-check artefact, not a market read. */}
                <article className="panel" aria-label="Index constituents">
                  <div className="panel-heading">
                    <h2>Constituents</h2>
                    <span className="status">{constituentList.length} members</span>
                  </div>
                  <AsyncBlock loading={constituents.loading} error={constituents.error}>
                    {constituentList.length === 0 ? (
                      <p>
                        No constituents for {index} as of {effectiveAsOf}.
                      </p>
                    ) : (
                      <ConstituentTable constituents={constituentList} />
                    )}
                  </AsyncBlock>
                </article>
              </div>
            );
          })()}
      </AsyncBlock>
    </section>
  );
}
