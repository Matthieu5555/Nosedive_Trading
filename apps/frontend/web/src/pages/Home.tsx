// The operator front page (WS 1I): pick an index → pick a recorded date → scroll the
// point-in-time constituent list → select a ticker → see its price-first detail layout
// (candlestick, 3D IV surface, per-maturity accordion of smile + dollar Greeks).
//
// Every panel is self-labelling and every number traces to a store-backed BFF endpoint
// (price-history, constituents, analytics, recorded-dates) through useFetch/AsyncBlock — no
// fixtures, no mocks. Selecting a past recorded date re-resolves the basket and analytics
// as-of that date (the as_of drives both queries); it is never defaulted to today.

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

function TickerDetail({ underlying, asOf }: { underlying: string; asOf: string }) {
  const priceState = useFetch<PriceHistoryResponse>(
    `/api/price-history?underlying=${encodeURIComponent(underlying)}&end=${encodeURIComponent(asOf)}`,
  );
  const analyticsState = useFetch<AnalyticsResponse>(
    `/api/analytics?underlying=${encodeURIComponent(underlying)}&trade_date=${encodeURIComponent(asOf)}`,
  );
  return (
    <section aria-label={`Detail for ${underlying}`}>
      <h2>{underlying}</h2>
      {/* Price-first: the candlestick leads, then the 3D surface, then the accordion. */}
      <AsyncBlock state={priceState}>{(data) => <PriceChart data={data} />}</AsyncBlock>
      <AsyncBlock state={analyticsState}>
        {(data) => (
          <>
            <VolSurface maturities={data.maturities} />
            <MaturityAccordion maturities={data.maturities} />
          </>
        )}
      </AsyncBlock>
    </section>
  );
}

export function HomePage() {
  const [index, setIndex] = useState("SPX");
  const [asOf, setAsOf] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const recordedState = useFetch<RecordedDatesResponse>(
    `/api/recorded-dates?index=${encodeURIComponent(index)}`,
  );
  // Until a date is picked, resolve as-of the most recent recorded date (set on first render of
  // the dropdown); the constituents query waits for an as_of so it never defaults to today.

  return (
    <section>
      <h1>Index analytics — front page</h1>
      <p>
        Pick an index and a recorded date, scroll its point-in-time constituents, then select a
        ticker to see its daily candlestick, vol surface, smile, and dollar Greeks.
      </p>

      <label>
        Index{" "}
        <input
          aria-label="index"
          value={index}
          onChange={(event) => {
            setIndex(event.target.value.toUpperCase());
            setSelected(null);
            setAsOf(null);
          }}
        />
      </label>

      <AsyncBlock state={recordedState}>
        {(recorded) => {
          const effectiveAsOf = asOf ?? recorded.dates[0] ?? null;
          return (
            <div>
              <p aria-label="recorded-count">{recorded.count} days recorded</p>
              {recorded.count === 0 ? (
                <p>No completed capture runs for {recorded.index} yet.</p>
              ) : (
                <>
                  <label>
                    As-of date{" "}
                    <select
                      aria-label="as-of date"
                      value={effectiveAsOf ?? ""}
                      onChange={(event) => {
                        setAsOf(event.target.value);
                        setSelected(null);
                      }}
                    >
                      {recorded.dates.map((date) => (
                        <option key={date} value={date}>
                          {date}
                        </option>
                      ))}
                    </select>
                  </label>
                  {effectiveAsOf !== null && (
                    <ConstituentList
                      index={index}
                      asOf={effectiveAsOf}
                      selected={selected}
                      onSelect={setSelected}
                    />
                  )}
                  {effectiveAsOf !== null && selected !== null && (
                    <TickerDetail underlying={selected} asOf={effectiveAsOf} />
                  )}
                </>
              )}
            </div>
          );
        }}
      </AsyncBlock>
    </section>
  );
}

function ConstituentList({
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
    <AsyncBlock state={state}>
      {(data) =>
        data.n_constituents === 0 ? (
          <p>No constituents for {data.index} as of {data.as_of}.</p>
        ) : (
          <ConstituentTable
            constituents={data.constituents}
            selected={selected}
            onSelect={onSelect}
          />
        )
      }
    </AsyncBlock>
  );
}
