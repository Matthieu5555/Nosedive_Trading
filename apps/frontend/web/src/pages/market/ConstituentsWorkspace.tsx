import { useEffect, useMemo } from "react";

import type {
  Constituent,
  ConstituentsResponse,
  PriceHistoryBatchResponse,
  PriceHistoryResponse,
} from "../../api";
import { AsyncBlock } from "../../components/AsyncBlock";
import { PriceChart } from "../../components/charts";
import { ConstituentTable } from "../../components/ConstituentTable";
import { useFetch } from "../../hooks/useFetch";
import { useConstituentHistoryBatch } from "./constituentHistory";

export function ConstituentsWorkspace({
  index,
  asOf,
  recordedIndex,
  recordedCount,
  selected,
  onSelect,
}: {
  index: string;
  asOf: string;
  recordedIndex: string;
  recordedCount: number;
  selected: string | null;
  onSelect: (symbol: string) => void;
}) {
  const state = useFetch<ConstituentsResponse>(
    `/api/constituents?index=${encodeURIComponent(index)}&as_of=${encodeURIComponent(asOf)}`,
  );
  const symbols = useMemo(
    () => state.data?.constituents.map((item) => item.symbol) ?? [],
    [state.data],
  );
  const histories = useConstituentHistoryBatch(symbols, asOf);
  const historyByUnderlying = useMemo(
    () =>
      new Map((histories.data?.histories ?? []).map((history) => [history.underlying, history])),
    [histories.data],
  );
  const selectedHistory = selected === null ? null : (historyByUnderlying.get(selected) ?? null);

  // Default-select the heaviest constituent once the basket loads (cahier des charges §3.2: the
  // top row is selected by default). Weight desc, nulls last — matches the table's default order.
  const heaviest = useMemo(() => {
    const list = state.data?.constituents ?? [];
    if (list.length === 0) return null;
    return [...list].sort((a, b) => (b.weight ?? -Infinity) - (a.weight ?? -Infinity))[0].symbol;
  }, [state.data]);
  useEffect(() => {
    if (selected === null && heaviest !== null) onSelect(heaviest);
  }, [selected, heaviest, onSelect]);

  return (
    <div className="constituents-row">
      <article className="panel stocks-panel">
        <div className="panel-heading">
          <h2>Constituents</h2>
          <span className="status">{recordedCount} days recorded</span>
        </div>
        <AsyncBlock loading={state.loading} error={state.error}>
          {state.data &&
            (state.data.n_constituents === 0 ? (
              <p>
                No constituents for {state.data.index} as of {state.data.as_of}.
              </p>
            ) : (
              <>
                <UnderlyingDataSummary
                  batch={histories.data}
                  loading={histories.loading}
                  error={histories.error}
                  constituents={state.data.constituents}
                />
                <ConstituentTable
                  constituents={state.data.constituents}
                  selected={selected}
                  onSelect={onSelect}
                />
              </>
            ))}
        </AsyncBlock>
      </article>

      <article
        className="panel component-panel"
        aria-label={selected ? `Price history for ${selected}` : "Component price history"}
      >
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{recordedIndex}</p>
            <h2>{selected ?? "Pick a ticker"}</h2>
          </div>
          <span className="status">full component history</span>
        </div>
        {selected === null ? (
          <p>Select a constituent on the left to see its price history.</p>
        ) : (
          <SelectedComponentHistory symbol={selected} asOf={asOf} batchEntry={selectedHistory} />
        )}
      </article>
    </div>
  );
}

// The selected ticker's candlestick must NOT wait on the whole-basket batch preload (~1 min
// on the live store — reloading the page restarts it, so the detail never appeared): the
// moment a ticker is selected it loads through the cheap single-ticker endpoint, and the
// batch entry simply takes over once the preload lands.
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
      {data && <PriceChart data={data} />}
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
      <div>
        <span>History window</span>
        <strong>{windowText}</strong>
      </div>
      {error !== null && <p role="alert">History batch failed: {error}</p>}
    </div>
  );
}
