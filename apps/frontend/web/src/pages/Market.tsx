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

import { useState } from "react";

import type {
  AnalyticsResponse,
  ConstituentsResponse,
  PriceHistoryResponse,
  RecordedDatesResponse,
} from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { ConstituentTable } from "../components/ConstituentTable";
import { MaturityAccordion } from "../components/MaturityAccordion";
import { PriceChart, VolSurface } from "../components/charts";
import { useFetch } from "../hooks/useFetch";

// The seeded index registry (roadmap 1J: SX5E first, SPX as the stretch target).
const INDICES = ["SPX", "SX5E"];

export function MarketPage() {
  const [index, setIndex] = useState("SPX");
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
            const effectiveAsOf = asOf ?? recorded.data.dates[0] ?? null;
            if (recorded.data.count === 0 || effectiveAsOf === null) {
              return (
                <article className="panel">
                  <p>No completed capture runs for {recorded.data.index} yet.</p>
                </article>
              );
            }
            return (
              <>
                {/* The index's own daily history leads the page (price-first). */}
                <article className="panel history-panel" aria-label={`${recorded.data.index} daily history`}>
                  <div className="panel-heading">
                    <div>
                      <p className="panel-kicker">{recorded.data.index}</p>
                      <h2>Index daily history</h2>
                    </div>
                    <span className="status">as of {effectiveAsOf}</span>
                  </div>
                  <IndexHistory underlying={recorded.data.index} asOf={effectiveAsOf} />
                </article>

                {/* Master-detail: constituents on the left, the picked ticker on the right. */}
                <div className="detail-grid">
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
                    className="panel detail-panel"
                    aria-label={selected ? `Detail for ${selected}` : "Ticker detail"}
                  >
                    <div className="panel-heading">
                      <div>
                        <p className="panel-kicker">{recorded.data.index}</p>
                        <h2>{selected ?? "Pick a ticker"}</h2>
                      </div>
                      <span className="status">price-first</span>
                    </div>
                    {selected === null ? (
                      <p>Select a constituent on the left to see its chart and analytics.</p>
                    ) : (
                      <TickerDetail underlying={selected} asOf={effectiveAsOf} />
                    )}
                  </article>
                </div>
              </>
            );
          })()}
      </AsyncBlock>
    </section>
  );
}

// The as-of date picker is populated from the recorded-dates response; until that loads it
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
  const dates = recorded?.dates ?? [];
  const effective = value ?? dates[0] ?? "";
  return (
    <select
      aria-label="As-of date"
      value={effective}
      disabled={dates.length === 0}
      onChange={(event) => onChange(event.target.value)}
    >
      {dates.length === 0 ? (
        <option value="">No recorded dates</option>
      ) : (
        dates.map((date) => (
          <option key={date} value={date}>
            {date}
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

function TickerDetail({ underlying, asOf }: { underlying: string; asOf: string }) {
  const price = useFetch<PriceHistoryResponse>(
    `/api/price-history?underlying=${encodeURIComponent(underlying)}&end=${encodeURIComponent(asOf)}`,
  );
  const analytics = useFetch<AnalyticsResponse>(
    `/api/analytics?underlying=${encodeURIComponent(underlying)}&trade_date=${encodeURIComponent(asOf)}`,
  );
  return (
    <>
      {/* Price-first: the ticker candlestick leads, then the 3D surface, then the per-maturity
          smile accordion (each maturity carries its dollar Greeks in decimal AND currency). */}
      <AsyncBlock loading={price.loading} error={price.error}>
        {price.data && <PriceChart data={price.data} />}
      </AsyncBlock>
      <AsyncBlock loading={analytics.loading} error={analytics.error}>
        {analytics.data && (
          <>
            <VolSurface maturities={analytics.data.maturities} />
            <MaturityAccordion maturities={analytics.data.maturities} />
          </>
        )}
      </AsyncBlock>
    </>
  );
}
