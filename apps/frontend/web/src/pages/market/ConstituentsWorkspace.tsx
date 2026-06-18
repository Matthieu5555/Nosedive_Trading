import { useEffect, useMemo, useState } from "react";

import type {
  Constituent,
  ConstituentsResponse,
  PriceHistoryBatchResponse,
  PriceHistoryResponse,
} from "../../api";
import { AsyncBlock } from "../../components/AsyncBlock";
import { PriceChart } from "../../components/charts";
import { ConstituentTable } from "../../components/ConstituentTable";
import { Scroll, Stack } from "../../components/layout";
import { useFetch } from "../../hooks/useFetch";
import { useConstituentHistoryBatch } from "./constituentHistory";

// The Constituents element. It is both a read (index members, weights, latest close, plus a
// master-detail candle for one member) AND the page's secondary selection surface: clicking a row
// makes that constituent the active ticker every panel above re-renders for. The constituents data is
// lifted to the page (so the top ticker selector and this table share one fetch); this component owns
// the per-member history batch and the detail candle.
export function ConstituentsWorkspace({
  asOf,
  currency,
  constituents,
  loading,
  error,
  activeTicker,
  onSelectConstituent,
}: {
  asOf: string;
  // The index quote-currency ISO code (EUR/USD/...), so latest close reads "€1,624.00".
  currency: string | null;
  constituents: ConstituentsResponse | null;
  loading: boolean;
  error: string | null;
  // The active ticker driving the whole page. When it is a constituent, the detail candle and the row
  // highlight follow it; when it is the index, the candle defaults to the heaviest member so the
  // member comparison still reads, without changing the active ticker.
  activeTicker: string;
  onSelectConstituent: (symbol: string) => void;
}) {
  const symbols = useMemo(
    () => constituents?.constituents.map((item) => item.symbol) ?? [],
    [constituents],
  );
  const histories = useConstituentHistoryBatch(symbols, asOf);
  const historyByUnderlying = useMemo(
    () =>
      new Map((histories.data?.histories ?? []).map((history) => [history.underlying, history])),
    [histories.data],
  );

  const memberSymbols = useMemo(() => new Set(symbols), [symbols]);
  const heaviest = useMemo(() => {
    const list = constituents?.constituents ?? [];
    if (list.length === 0) return null;
    return [...list].sort((a, b) => (b.weight ?? -Infinity) - (a.weight ?? -Infinity))[0].symbol;
  }, [constituents]);

  // The detail candle's member: the active ticker when it is one of these members, else a local
  // default (the heaviest), so a PM lands on a real member comparison without the active ticker
  // leaving the index.
  const [detailMember, setDetailMember] = useState<string | null>(null);
  useEffect(() => {
    if (detailMember === null && heaviest !== null) setDetailMember(heaviest);
  }, [detailMember, heaviest]);
  const detail = memberSymbols.has(activeTicker) ? activeTicker : detailMember;
  const detailHistory = detail === null ? null : (historyByUnderlying.get(detail) ?? null);

  // A row click sets the active ticker (the page-driving action) AND fixes the detail candle on that
  // member, so the click reads as one gesture: "show me this name everywhere".
  const handleSelect = (symbol: string) => {
    setDetailMember(symbol);
    onSelectConstituent(symbol);
  };

  return (
    <div className="constituents-row">
      <article className="panel stocks-panel">
        <Stack gap="md">
          <div className="panel-heading">
            <h2>Constituents</h2>
            <span className="status">
              {constituents ? `${constituents.n_constituents} members` : ""}
            </span>
          </div>
          <AsyncBlock loading={loading} error={error}>
            {constituents &&
              (constituents.n_constituents === 0 ? (
                <p>
                  No constituents for {constituents.index} as of {constituents.as_of}.
                </p>
              ) : (
                <Stack gap="md">
                  <UnderlyingDataSummary
                    batch={histories.data}
                    loading={histories.loading}
                    error={histories.error}
                    constituents={constituents.constituents}
                  />
                  <ConstituentTable
                    constituents={constituents.constituents}
                    currency={currency}
                    selected={detail}
                    onSelect={handleSelect}
                  />
                </Stack>
              ))}
          </AsyncBlock>
        </Stack>
      </article>

      <article
        className="panel component-panel"
        aria-label={detail ? `Price history for ${detail}` : "Component price history"}
      >
        <Stack gap="md">
          <div className="panel-heading">
            <h2>{detail ?? "Pick a ticker"}</h2>
            <span className="status">selected member · daily OHLC</span>
          </div>
          {detail === null ? (
            <p>Select a constituent on the left to see its price history.</p>
          ) : (
            <SelectedComponentHistory symbol={detail} asOf={asOf} batchEntry={detailHistory} />
          )}
        </Stack>
      </article>
    </div>
  );
}

function SelectedComponentHistory({
  symbol,
  asOf,
  batchEntry,
}: {
  symbol: string;
  asOf: string;
  batchEntry: PriceHistoryResponse | null;
}) {
  const single = useFetch<PriceHistoryResponse>(
    `/api/price-history?underlying=${encodeURIComponent(symbol)}&end=${encodeURIComponent(asOf)}`,
  );
  const data = batchEntry ?? single.data;
  return (
    <AsyncBlock
      loading={data === null && single.loading}
      error={data === null ? single.error : null}
    >
      {data && (
        <Scroll label={`${symbol} price chart`}>
          <PriceChart data={data} />
        </Scroll>
      )}
    </AsyncBlock>
  );
}

function UnderlyingDataSummary({
  batch,
  loading,
  error,
  constituents,
}: {
  batch: PriceHistoryBatchResponse | null;
  loading: boolean;
  error: string | null;
  constituents: Constituent[];
}) {
  const expected = constituents.length;
  const coverage = batch === null ? 0 : Math.round((batch.n_loaded / Math.max(1, expected)) * 100);
  const tradeDates = (batch?.histories ?? []).flatMap((history) =>
    history.bars.map((bar) => bar.trade_date),
  );
  const firstDate = tradeDates.length === 0 ? null : tradeDates.reduce((a, b) => (a < b ? a : b));
  const lastDate = tradeDates.length === 0 ? null : tradeDates.reduce((a, b) => (a > b ? a : b));
  const windowText = loading
    ? "loading"
    : firstDate === null || lastDate === null
      ? "n/a"
      : `${firstDate} - ${lastDate}`;
  return (
    <div className="underlying-data-summary" aria-label="Underlying history coverage">
      <div>
        <span>Underlying histories</span>
        <strong>
          {loading
            ? "loading"
            : batch === null
              ? `0 / ${expected}`
              : `${batch.n_loaded} / ${expected}`}
        </strong>
      </div>
      <div>
        <span>Bars loaded</span>
        <strong>{batch === null ? "0" : batch.n_bars.toLocaleString()}</strong>
      </div>
      <div>
        <span>Empty histories</span>
        <strong>{loading ? "loading" : batch === null ? "0" : batch.n_empty}</strong>
      </div>
      <div>
        <span>Coverage</span>
        <strong>{loading ? "..." : `${coverage}%`}</strong>
      </div>
      <div className="underlying-data-summary__wide">
        <span>History window</span>
        <strong>{windowText}</strong>
      </div>
      {error !== null && <p role="alert">History batch failed: {error}</p>}
    </div>
  );
}
