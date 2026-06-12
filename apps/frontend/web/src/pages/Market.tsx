// Tab 1 — the data foundation (roadmap 1I), wearing Antho's panel grammar.
//
// Page-1 layout (price-first):
//   • the INDEX's own daily OHLC candlestick leads the page;
//   • below, a master-detail row — the scrollable point-in-time constituent list on the LEFT,
//     the selected ticker's detail on the RIGHT (its daily candlestick, 3D IV surface, and a
//     per-maturity accordion of the smile + dollar Greeks in decimal AND currency).
//
// This file is the page SHELL only: index/as-of selection state and the composition of the
// self-fetching panels (IndexHistory, ConstituentsWorkspace, IndexAnalytics, CoveragePanel),
// each of which owns its own BFF fetch and its own ErrorBoundary — so one panel throwing (a
// Plotly choke on a degenerate vol-surface cell, say) degrades to a labelled tile and the rest
// of the page survives, instead of unwinding the whole tab to a blank screen.

import { useState } from "react";

import type { RecordedDatesResponse } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { CoveragePanel } from "../components/CoverageTable";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { useFetch } from "../hooks/useFetch";
import { ConstituentsWorkspace } from "./market/ConstituentsWorkspace";
import { IndexAnalytics, IndexHistory } from "./market/IndexAnalytics";
import { AsOfSelect, QcBadge } from "./market/marketHeader";

// Re-exported here because the test imports it from `./Market`; the cache itself lives with the
// batch hook it guards (`./market/constituentHistory`).
export { resetConstituentHistoryBatchCacheForTests } from "./market/constituentHistory";

// The seeded index registry (roadmap 1J: SX5E first, SPX as the stretch target).
const INDICES = ["SPX", "SX5E"];

export function MarketPage() {
  // Default to SX5E: it is the index currently captured (SPX's option chain isn't captured yet),
  // so the page lands on data. Flip back to SPX once SPX has its own snapshots.
  const [index, setIndex] = useState("SX5E");
  const [asOf, setAsOf] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const recorded = useFetch<RecordedDatesResponse>(
    `/api/recorded-dates?index=${encodeURIComponent(index)}`,
  );

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Index data foundation</p>
          <h1>Market</h1>
        </div>
        <div className="control-row">
          <select
            aria-label="Index"
            value={index}
            onChange={(event) => {
              setIndex(event.target.value);
              setAsOf(null);
              setSelected(null);
            }}
          >
            {INDICES.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
          <AsOfSelect
            recorded={recorded.data}
            value={asOf}
            onChange={(date) => {
              setAsOf(date);
              setSelected(null);
            }}
          />
        </div>
      </div>

      <AsyncBlock loading={recorded.loading} error={recorded.error}>
        {recorded.data &&
          (() => {
            // The picker offers every viewable day (incl. qc-failing ones), not only the
            // clean ones, so a degraded snapshot is selectable and shown with its QC badge.
            // The DEFAULT, though, is the latest QC-passing day: landing on a failing
            // (e.g. intraday) capture renders degraded panels before the operator chose
            // anything. A failing day stays one click away in the picker.
            const available = recorded.data.available ?? [];
            const effectiveAsOf =
              asOf ??
              available.find((day) => day.qc === "pass")?.date ??
              available[0]?.date ??
              null;
            if (available.length === 0 || effectiveAsOf === null) {
              return (
                <article className="panel">
                  <p>No capture runs to show for {recorded.data.index} yet.</p>
                </article>
              );
            }
            const qc = available.find((a) => a.date === effectiveAsOf)?.qc ?? "unknown";
            const recordedIndex = recorded.data.index;
            return (
              <>
                {/* The index's own daily history leads the page (price-first). */}
                <article className="panel history-panel" aria-label={`${recordedIndex} daily history`}>
                  <div className="panel-heading">
                    <div>
                      <p className="panel-kicker">{recordedIndex}</p>
                      <h2>Index daily history</h2>
                    </div>
                    <span className="status">
                      as of {effectiveAsOf} <QcBadge qc={qc} />
                    </span>
                  </div>
                  <ErrorBoundary label="Index history">
                    <IndexHistory underlying={recordedIndex} asOf={effectiveAsOf} />
                  </ErrorBoundary>
                </article>

                {/* Constituents (left) + all constituent histories loaded in one batch, with the
                    selected component's price history shown on the right. */}
                <ErrorBoundary label="Constituents">
                  <ConstituentsWorkspace
                    index={index}
                    asOf={effectiveAsOf}
                    recordedIndex={recordedIndex}
                    recordedCount={recorded.data.count}
                    selected={selected}
                    onSelect={setSelected}
                  />
                </ErrorBoundary>

                {/* The INDEX's volatility analytics (nappe / greeks / smile), full width below the
                    row. The option chain is captured at the index level, not per constituent, so
                    these always track the index — the constituent selection only drives its price
                    chart above (cahier des charges §3.4–3.6). */}
                <article
                  className="panel analytics-panel"
                  aria-label={`Volatility analytics for ${recordedIndex}`}
                >
                  <ErrorBoundary label="Volatility analytics">
                    <IndexAnalytics underlying={recordedIndex} asOf={effectiveAsOf} />
                  </ErrorBoundary>
                </article>

                {/* Capture coverage: the captured chain as a plain quality table (per-expiry +
                    per-tenor QC). The surface above smooths over gaps; this shows them, so a
                    term-structure hole (1m…3y empty) or a thin strike window is visible at a
                    glance — the data-quality readout behind the analytics. */}
                <article
                  className="panel coverage-panel"
                  aria-label={`Capture coverage for ${recordedIndex}`}
                >
                  <ErrorBoundary label="Capture coverage">
                    <CoveragePanel underlying={recordedIndex} tradeDate={effectiveAsOf ?? undefined} />
                  </ErrorBoundary>
                </article>
              </>
            );
          })()}
      </AsyncBlock>
    </section>
  );
}
