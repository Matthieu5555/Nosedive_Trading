import { useEffect, useMemo, useState } from "react";

import type { IndicesResponse, RecordedDatesResponse } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { CoveragePanel } from "../components/CoverageTable";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { useFetch } from "../hooks/useFetch";
import { currencySymbol } from "../lib/format";
import { ConstituentsWorkspace } from "./market/ConstituentsWorkspace";
import { IndexAnalytics, IndexHistory } from "./market/IndexAnalytics";
import { AsOfSelect, QcBadge } from "./market/marketHeader";

export { resetConstituentHistoryBatchCacheForTests } from "./market/constituentHistory";

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

  const [asOf, setAsOf] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const recorded = useFetch<RecordedDatesResponse>(
    index ? `/api/recorded-dates?index=${encodeURIComponent(index)}` : "",
  );

  // The default as-of must be ONE value, shared by the picker and the panels. Computed in two
  // places it silently drifts: the picker defaulted to the newest day (available[0]) while the
  // panels defaulted to the newest QC-passing day — so the header showed one date while the data
  // on screen was another, and re-selecting the date the picker already displayed fired no change
  // event, leaving the real day unreachable. So compute it once, here.
  //
  // The DEFAULT is the latest available day (available[] is newest-first), not the latest
  // QC-passing day: the freshest capture is what an operator opens the page to see, and its
  // quality is already announced by the QC badge next to the date — hiding it by default just
  // because QC failed left the page blank whenever the newest snapshot was the only one carrying
  // analytics (the live case: the QC-passing days predate the projected-analytics backfill).
  const available = recorded.data?.available ?? [];
  const effectiveAsOf = asOf ?? available[0]?.date ?? null;

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
            disabled={indexOptions.length === 0}
            onChange={(event) => {
              setIndex(event.target.value);
              setAsOf(null);
              setSelected(null);
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
            value={effectiveAsOf}
            onChange={(date) => {
              setAsOf(date);
              setSelected(null);
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
            const qc = available.find((a) => a.date === effectiveAsOf)?.qc ?? "unknown";
            const recordedIndex = recorded.data.index;
            // The quote-currency SYMBOL of the index being viewed (€ for SX5E, $ for SPX), from
            // the registry — so the analytics panel's monetized Greeks render in the right
            // currency, never a hard-coded "$". Unknown/missing → "$".
            const currency = currencySymbol(
              indexOptions.find((o) => o.symbol === recordedIndex)?.currency,
            );
            return (
              <>
                {/* The index's own daily history leads the page (price-first). */}
                <article
                  className="panel history-panel"
                  aria-label={`${recordedIndex} daily history`}
                >
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
                    <IndexAnalytics
                      underlying={recordedIndex}
                      asOf={effectiveAsOf}
                      currency={currency}
                    />
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
                    <CoveragePanel
                      underlying={recordedIndex}
                      tradeDate={effectiveAsOf ?? undefined}
                    />
                  </ErrorBoundary>
                </article>
              </>
            );
          })()}
      </AsyncBlock>
    </section>
  );
}
