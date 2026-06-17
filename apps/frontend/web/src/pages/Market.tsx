import { useEffect, useMemo, useState } from "react";

import {
  type AnalyticsResponse,
  type IndicesResponse,
  type PriceHistoryResponse,
  type RecordedDatesResponse,
  type SignalsResponse,
} from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { PriceChart, VolSurface } from "../components/charts";
import { CoveragePanel } from "../components/CoverageTable";
import { DispersionStrip } from "../components/DispersionStrip";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Scorecards } from "../components/Scorecards";
import { TenorPanel } from "../components/TenorPanel";
import { useFetch } from "../hooks/useFetch";
import { currencySymbol } from "../lib/format";
import { ConstituentsWorkspace } from "./market/ConstituentsWorkspace";
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

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [selectedMember, setSelectedMember] = useState<string | null>(null);
  const [coverageOpen, setCoverageOpen] = useState(false);

  const recorded = useFetch<RecordedDatesResponse>(
    index ? `/api/recorded-dates?index=${encodeURIComponent(index)}` : "",
  );

  const available = recorded.data?.available ?? [];
  const selectedFetch =
    available.find((fetch) => (fetch.run_id ?? fetch.date) === selectedKey) ?? available[0] ?? null;
  const effectiveRunId = selectedFetch?.run_id ?? null;
  const effectiveAsOf = selectedFetch?.date ?? null;
  const selectedFetchKey = selectedFetch ? (selectedFetch.run_id ?? selectedFetch.date) : null;

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
  const signals = useFetch<SignalsResponse>(
    index && effectiveAsOf
      ? `/api/signals?underlying=${encodeURIComponent(index)}&trade_date=${encodeURIComponent(effectiveAsOf)}`
      : "",
  );

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
              setSelectedMember(null);
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

                <ErrorBoundary label="Scorecards">
                  <AsyncBlock
                    loading={analytics.loading || signals.loading}
                    error={analytics.error}
                  >
                    {analytics.data && (
                      <Scorecards
                        maturities={analytics.data.maturities}
                        ivVsRealized={signals.data?.by_kind?.iv_vs_realized?.[0] ?? null}
                        termStructureSlope={
                          signals.data?.by_kind?.term_structure_slope?.[0] ?? null
                        }
                        ivRank={signals.data?.by_kind?.iv_rank?.[0] ?? null}
                        impliedCorrelation={
                          signals.data?.by_kind?.implied_correlation?.[0] ?? null
                        }
                      />
                    )}
                  </AsyncBlock>
                </ErrorBoundary>

                <article className="panel" aria-label={`${index} daily history`}>
                  <div className="panel-heading">
                    <div>
                      <p className="panel-kicker">{index}</p>
                      <h2>Price</h2>
                    </div>
                    <span className="status">index daily OHLC</span>
                  </div>
                  <ErrorBoundary label="Price">
                    <AsyncBlock loading={price.loading} error={price.error}>
                      {price.data && <PriceChart data={price.data} />}
                    </AsyncBlock>
                  </ErrorBoundary>
                </article>

                <ErrorBoundary label="Constituents">
                  <ConstituentsWorkspace
                    index={index}
                    asOf={effectiveAsOf}
                    selected={selectedMember}
                    onSelect={setSelectedMember}
                  />
                </ErrorBoundary>

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

                <ErrorBoundary label="Tenor view">
                  <AsyncBlock loading={analytics.loading} error={analytics.error}>
                    {analytics.data && (
                      <TenorPanel maturities={analytics.data.maturities} currency={currency} />
                    )}
                  </AsyncBlock>
                </ErrorBoundary>

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

                <article className="panel" aria-label="Capture coverage">
                  <div className="panel-heading">
                    <div>
                      <p className="panel-kicker">{index}</p>
                      <h2>Capture coverage</h2>
                    </div>
                    <button
                      type="button"
                      aria-expanded={coverageOpen}
                      onClick={() => setCoverageOpen((open) => !open)}
                    >
                      {coverageOpen ? "Hide" : "Show"}
                    </button>
                  </div>
                  {coverageOpen && (
                    <ErrorBoundary label="Capture coverage">
                      <CoveragePanel
                        underlying={index}
                        tradeDate={effectiveAsOf}
                        runId={effectiveRunId ?? undefined}
                      />
                    </ErrorBoundary>
                  )}
                </article>
              </div>
            );
          })()}
      </AsyncBlock>
    </section>
  );
}
