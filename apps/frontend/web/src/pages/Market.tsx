// Tab 1 — the data foundation (roadmap 1I), wearing Antho's panel grammar.
//
// Page-1 layout (price-first):
//   • the INDEX's own daily OHLC candlestick leads the page;
//   • below, a master-detail row — the scrollable point-in-time constituent list on the LEFT,
//     the selected ticker's detail on the RIGHT (its daily candlestick, 3D IV surface, and a
//     per-maturity accordion of the smile + dollar Greeks in decimal AND currency).
//
// Every panel is store-backed through the real BFF (/api/recorded-dates, /api/constituents,
// /api/price-history for both the index and the ticker, /api/analytics) — no fixtures. Picking
// a past recorded date re-resolves the basket and analytics as-of that date.

import { useEffect, useMemo, useState } from "react";

import type {
  AnalyticsResponse,
  ConstituentsResponse,
  PriceHistoryResponse,
  QcVerdict,
  RecordedDatesResponse,
} from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { ConstituentTable } from "../components/ConstituentTable";
import { MaturityAccordion } from "../components/MaturityAccordion";
import { GreeksTermStructure, PriceChart, VolSurface } from "../components/charts";
import { useFetch } from "../hooks/useFetch";

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
            const available = recorded.data.available ?? [];
            const effectiveAsOf = asOf ?? available[0]?.date ?? null;
            if (available.length === 0 || effectiveAsOf === null) {
              return (
                <article className="panel">
                  <p>No capture runs to show for {recorded.data.index} yet.</p>
                </article>
              );
            }
            const qc = available.find((a) => a.date === effectiveAsOf)?.qc ?? "unknown";
            return (
              <>
                {/* The index's own daily history leads the page (price-first). */}
                <article className="panel history-panel" aria-label={`${recorded.data.index} daily history`}>
                  <div className="panel-heading">
                    <div>
                      <p className="panel-kicker">{recorded.data.index}</p>
                      <h2>Index daily history</h2>
                    </div>
                    <span className="status">
                      as of {effectiveAsOf} <QcBadge qc={qc} />
                    </span>
                  </div>
                  <IndexHistory underlying={recorded.data.index} asOf={effectiveAsOf} />
                </article>

                {/* Constituents (left) + the selected component's price history (right), two
                    equal-height blocks; the component chart is narrower than the full-width index
                    chart above and shares the list's row (cahier des charges §3.2). */}
                <div className="constituents-row">
                  <article className="panel stocks-panel">
                    <div className="panel-heading">
                      <h2>Constituents</h2>
                      <span className="status">{recorded.data.count} days recorded</span>
                    </div>
                    <ConstituentPanel
                      index={index}
                      asOf={effectiveAsOf}
                      selected={selected}
                      onSelect={setSelected}
                    />
                  </article>

                  <article
                    className="panel component-panel"
                    aria-label={selected ? `Price history for ${selected}` : "Component price history"}
                  >
                    <div className="panel-heading">
                      <div>
                        <p className="panel-kicker">{recorded.data.index}</p>
                        <h2>{selected ?? "Pick a ticker"}</h2>
                      </div>
                      <span className="status">component history</span>
                    </div>
                    {selected === null ? (
                      <p>Select a constituent on the left to see its price history.</p>
                    ) : (
                      <ComponentHistory underlying={selected} asOf={effectiveAsOf} />
                    )}
                  </article>
                </div>

                {/* The INDEX's volatility analytics (nappe / greeks / smile), full width below the
                    row. The option chain is captured at the index level, not per constituent, so
                    these always track the index — the constituent selection only drives its price
                    chart above (cahier des charges §3.4–3.6). */}
                <article
                  className="panel analytics-panel"
                  aria-label={`Volatility analytics for ${recorded.data.index}`}
                >
                  <IndexAnalytics underlying={recorded.data.index} asOf={effectiveAsOf} />
                </article>
              </>
            );
          })()}
      </AsyncBlock>
    </section>
  );
}

// A QC verdict chip (pass / fail / unknown) — so a degraded snapshot is shown, not hidden
// (cahier des charges §3.1). The colour comes from a CSS class, never a hardcoded hex.
function QcBadge({ qc }: { qc: QcVerdict }) {
  const text = qc === "pass" ? "QC pass" : qc === "fail" ? "QC fail" : "QC n/a";
  return (
    <span className={`qc-badge qc-badge--${qc}`} aria-label={`QC ${qc}`}>
      {text}
    </span>
  );
}

// The as-of date picker, populated from the recorded-dates response's ``available`` list (every
// viewable day, incl. qc-failing ones — each option carries its QC verdict). Until it loads it
// shows a single disabled placeholder so the header layout is stable.
function AsOfSelect({
  recorded,
  value,
  onChange,
}: {
  recorded: RecordedDatesResponse | null;
  value: string | null;
  onChange: (date: string) => void;
}) {
  const available = recorded?.available ?? [];
  const effective = value ?? available[0]?.date ?? "";
  return (
    <select
      aria-label="As-of date"
      value={effective}
      disabled={available.length === 0}
      onChange={(event) => onChange(event.target.value)}
    >
      {available.length === 0 ? (
        <option value="">No recorded dates</option>
      ) : (
        available.map(({ date, qc }) => (
          <option key={date} value={date}>
            {date}
            {qc === "fail" ? " (QC fail)" : qc === "unknown" ? " (QC n/a)" : ""}
          </option>
        ))
      )}
    </select>
  );
}

function IndexHistory({ underlying, asOf }: { underlying: string; asOf: string }) {
  const price = useFetch<PriceHistoryResponse>(
    `/api/price-history?underlying=${encodeURIComponent(underlying)}&end=${encodeURIComponent(asOf)}`,
  );
  return (
    <AsyncBlock loading={price.loading} error={price.error}>
      {price.data && <PriceChart data={price.data} />}
    </AsyncBlock>
  );
}

function ConstituentPanel({
  index,
  asOf,
  selected,
  onSelect,
}: {
  index: string;
  asOf: string;
  selected: string | null;
  onSelect: (symbol: string) => void;
}) {
  const state = useFetch<ConstituentsResponse>(
    `/api/constituents?index=${encodeURIComponent(index)}&as_of=${encodeURIComponent(asOf)}`,
  );

  // Default-select the heaviest constituent once the basket loads (cahier des charges §3.2: the
  // top row is selected by default). Weight desc, nulls last — matches the table's default order.
  const heaviest = useMemo(() => {
    const list = state.data?.constituents ?? [];
    if (list.length === 0) return null;
    return [...list].sort(
      (a, b) => (b.weight ?? -Infinity) - (a.weight ?? -Infinity),
    )[0].symbol;
  }, [state.data]);
  useEffect(() => {
    if (selected === null && heaviest !== null) onSelect(heaviest);
  }, [selected, heaviest, onSelect]);

  return (
    <AsyncBlock loading={state.loading} error={state.error}>
      {state.data &&
        (state.data.n_constituents === 0 ? (
          <p>
            No constituents for {state.data.index} as of {state.data.as_of}.
          </p>
        ) : (
          <ConstituentTable
            constituents={state.data.constituents}
            selected={selected}
            onSelect={onSelect}
          />
        ))}
    </AsyncBlock>
  );
}

// The selected component's daily candlestick — fills the right side of the constituents row.
function ComponentHistory({ underlying, asOf }: { underlying: string; asOf: string }) {
  const price = useFetch<PriceHistoryResponse>(
    `/api/price-history?underlying=${encodeURIComponent(underlying)}&end=${encodeURIComponent(asOf)}`,
  );
  return (
    <AsyncBlock loading={price.loading} error={price.error}>
      {price.data && <PriceChart data={price.data} />}
    </AsyncBlock>
  );
}

// The INDEX's volatility analytics, full width below the constituents row: the 3D surface, the
// dollar-Greeks term structure (the curve view), then the per-maturity smile accordion (each
// maturity carries its dollar Greeks in decimal AND currency). The option chain is captured at
// the index level (SX5E/SPX), so this is index-keyed — not the selected constituent.
function IndexAnalytics({ underlying, asOf }: { underlying: string; asOf: string }) {
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
