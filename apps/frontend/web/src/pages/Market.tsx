import { useEffect, useMemo, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/ui/tabs";

import {
  ALL_MATURITIES,
  type AnalyticsResponse,
  type ConstituentsResponse,
  type IndicesResponse,
  type OptionSide,
  type RecordedDatesResponse,
} from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { useFetch } from "../hooks/useFetch";
import { currencySymbol } from "../lib/format";
import { AnalyticsTab } from "./market/AnalyticsTab";
import { DataQualityTab } from "./market/DataQualityTab";
import { AsOfSelect, QcBadge } from "./market/marketHeader";
import { SelectorStrip } from "./market/SelectorStrip";

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

  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  // The analytics entity: the index itself, or one of its members. Defaults to the index.
  const [entity, setEntity] = useState<string | null>(null);
  // Default to the downside wing: for an index the put skew is the interesting read, and it's the
  // side the book is typically short.
  const [side, setSide] = useState<OptionSide>("put");
  // Default to the whole term structure — the surface's natural read — not a single tenor.
  const [maturityLabel, setMaturityLabel] = useState<string>(ALL_MATURITIES);

  const recorded = useFetch<RecordedDatesResponse>(
    index ? `/api/recorded-dates?index=${encodeURIComponent(index)}` : "",
  );

  // The selection is ONE fetch (capture run), shared by the picker and the panels — computed once
  // here so the header and the data on screen can never drift apart. ``available`` is one row per
  // fetch, newest-first; the DEFAULT is the freshest fetch (available[0]), its quality announced by
  // the QC badge. From the chosen fetch we derive its run_id (addresses that fetch's
  // analytics/coverage — no other fetch can overwrite it) and its trade date (drives the
  // cross-date constituents panel, which is not per-fetch).
  const available = recorded.data?.available ?? [];
  const selectedFetch =
    available.find((fetch) => fetch.run_id === selectedRunId) ?? available[0] ?? null;
  const effectiveRunId = selectedFetch?.run_id ?? null;
  const effectiveAsOf = selectedFetch?.date ?? null;
  // The entity defaults to the index, and falls back to it whenever the index changes.
  const effectiveEntity = entity ?? index;
  const isIndex = effectiveEntity === index;

  const constituents = useFetch<ConstituentsResponse>(
    index && effectiveAsOf
      ? `/api/constituents?index=${encodeURIComponent(index)}&as_of=${encodeURIComponent(effectiveAsOf)}`
      : "",
  );
  const constituentList = useMemo(() => constituents.data?.constituents ?? [], [constituents.data]);

  const analytics = useFetch<AnalyticsResponse>(
    effectiveEntity && effectiveAsOf
      ? `/api/analytics?underlying=${encodeURIComponent(effectiveEntity)}&trade_date=${encodeURIComponent(effectiveAsOf)}` +
          (effectiveRunId ? `&run_id=${encodeURIComponent(effectiveRunId)}` : "")
      : "",
  );
  const maturityOptions = useMemo(
    () => (analytics.data?.maturities ?? []).map((m) => m.label),
    [analytics.data],
  );
  // Keep the chosen maturity valid as the entity/date changes; "all maturities" is always valid,
  // and is the fallback when a once-selected tenor is no longer captured.
  useEffect(() => {
    if (maturityOptions.length === 0) return;
    if (maturityLabel !== ALL_MATURITIES && !maturityOptions.includes(maturityLabel)) {
      setMaturityLabel(ALL_MATURITIES);
    }
  }, [maturityOptions, maturityLabel]);

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
              setSelectedRunId(null);
              setEntity(null);
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
            value={effectiveRunId}
            onChange={(runId) => {
              setSelectedRunId(runId);
              setEntity(null);
            }}
          />
        </div>
      </div>

      <AsyncBlock loading={indices.loading || recorded.loading} error={recorded.error}>
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
              <Tabs defaultValue="analytics" className="market-tabs">
                <div className="market-tabs__bar">
                  <TabsList className="market-tabs__list">
                    <TabsTrigger value="analytics">Analytics</TabsTrigger>
                    <TabsTrigger value="dataquality">Data quality</TabsTrigger>
                  </TabsList>
                  <span className="status">
                    as of {effectiveAsOf} <QcBadge qc={qc} />
                  </span>
                </div>

                <TabsContent value="analytics">
                  <div className="analytics-stack">
                    <SelectorStrip
                      index={index}
                      entity={effectiveEntity}
                      constituents={constituentList}
                      onEntity={(symbol) => setEntity(symbol)}
                      side={side}
                      onSide={setSide}
                      maturityLabel={maturityLabel}
                      maturityOptions={maturityOptions}
                      onMaturity={setMaturityLabel}
                    />
                    <ErrorBoundary label="Analytics">
                      <AsyncBlock loading={analytics.loading} error={analytics.error}>
                        {analytics.data && (
                          <AnalyticsTab
                            index={index}
                            entity={effectiveEntity}
                            isIndex={isIndex}
                            asOf={effectiveAsOf}
                            analytics={analytics.data}
                            side={side}
                            maturityLabel={maturityLabel}
                            constituents={constituentList}
                            currency={currency}
                          />
                        )}
                      </AsyncBlock>
                    </ErrorBoundary>
                  </div>
                </TabsContent>

                <TabsContent value="dataquality">
                  <AsyncBlock loading={constituents.loading} error={constituents.error}>
                    <DataQualityTab
                      index={index}
                      asOf={effectiveAsOf}
                      runId={effectiveRunId ?? undefined}
                      constituents={constituentList}
                      entity={effectiveEntity}
                      onEntity={(symbol) => setEntity(symbol)}
                    />
                  </AsyncBlock>
                </TabsContent>
              </Tabs>
            );
          })()}
      </AsyncBlock>
    </section>
  );
}
