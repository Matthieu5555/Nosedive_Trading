import { useEffect, useMemo, useState } from "react";

import type { SignalsResponse, SignalUnderlyingsResponse } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Cluster, Stack } from "../components/layout";
import { SignalsView } from "../components/SignalsView";
import { useFetch } from "../hooks/useFetch";

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function SignalsPage() {
  const underlyings = useFetch<SignalUnderlyingsResponse>("/api/signals/underlyings");
  const underlyingOptions = useMemo(() => underlyings.data?.underlyings ?? [], [underlyings.data]);

  const [underlying, setUnderlying] = useState("");
  useEffect(() => {
    if (underlyingOptions.length === 0) return;
    if (!underlying || !underlyingOptions.includes(underlying)) {
      setUnderlying(underlyingOptions[0]);
    }
  }, [underlyingOptions, underlying]);

  // Optional trade-date pin. Empty resolves the latest persisted partition; a well-formed date
  // pins it. Only a syntactically valid date fires a request, so a half-typed value never 400s.
  const [tradeDate, setTradeDate] = useState("");
  const dateValid = tradeDate === "" || ISO_DATE.test(tradeDate);

  const query =
    underlying && dateValid
      ? `/api/signals?underlying=${encodeURIComponent(underlying)}` +
        (tradeDate ? `&trade_date=${encodeURIComponent(tradeDate)}` : "")
      : "";
  const signals = useFetch<SignalsResponse>(query);

  const noUnderlyings =
    !underlyings.loading && !underlyings.error && underlyingOptions.length === 0;

  return (
    <Stack as="section" className="page" gap="md">
      <div className="page-header">
        <div>
          <p className="eyebrow">Strategy signal layer</p>
          <h1>Signals</h1>
        </div>
        <Cluster className="control-row" gap="sm" align="end">
          <select
            aria-label="Underlying"
            value={underlying}
            disabled={underlyingOptions.length === 0}
            onChange={(event) => setUnderlying(event.target.value)}
          >
            {underlyingOptions.map((symbol) => (
              <option key={symbol} value={symbol}>
                {symbol}
              </option>
            ))}
          </select>
          <input
            type="date"
            aria-label="Trade date"
            value={tradeDate}
            onChange={(event) => setTradeDate(event.target.value)}
          />
        </Cluster>
      </div>

      <p className="panel-note signals-lede">
        What the strategy layer measured at the close: how rich options look, whether the market
        moved more or less than they priced, the shape of the term structure, and how tightly the
        index members are expected to move together. Each block below says what it is and how to
        read it.
      </p>

      {/* The underlyings list gates the page. If it fails there is no selector, so its error must
          front the page rather than leave a dead, disabled dropdown. */}
      <AsyncBlock loading={underlyings.loading} error={underlyings.error}>
        {noUnderlyings ? (
          <div className="state-panel" role="status">
            No data yet
          </div>
        ) : (
          <ErrorBoundary label="Signals">
            <AsyncBlock loading={signals.loading} error={signals.error}>
              {signals.data && <SignalsView data={signals.data} />}
            </AsyncBlock>
          </ErrorBoundary>
        )}
      </AsyncBlock>
    </Stack>
  );
}
