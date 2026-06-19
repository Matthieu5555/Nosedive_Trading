import { useMemo, useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";

import type { BacktestResult, BacktestRunRequest, IndicesResponse } from "../api";
import { runBacktest } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { BacktestForm } from "../components/BacktestForm";
import { BacktestResults } from "../components/BacktestResults";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { Stack } from "../components/layout";
import { useFetch } from "../hooks/useFetch";
import { currencySymbol } from "../lib/format";
import { tourAnchor } from "../lib/tour";

export function StrategyPage() {
  const indices = useFetch<IndicesResponse>("/api/indices");
  const indexOptions = useMemo(() => indices.data?.indices ?? [], [indices.data]);

  const [result, setResult] = useState<BacktestResult | null>(null);
  const [ranIndex, setRanIndex] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  async function run(request: BacktestRunRequest) {
    setError(null);
    setRunning(true);
    try {
      const next = await runBacktest(request);
      setResult(next);
      setRanIndex(request.index);
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  }

  const currency = currencySymbol(indexOptions.find((o) => o.symbol === ranIndex)?.currency);

  return (
    <Stack as="section" className="page" gap="md">
      <div className="page-header">
        <div>
          <p className="eyebrow">Test a strategy on the days you have captured</p>
          <h1>Strategy</h1>
        </div>
      </div>

      <p>
        Backtest the short index put line over the offline store and read, at a glance,{" "}
        <strong>where the return came from</strong>. Set the window and the line&apos;s rules, run
        it, and the page shows the cumulative P&amp;L (before and after costs), the headline
        scorecard, the by-Greek attribution, and how the risk exposure moved day by day.
      </p>

      {indices.error !== null && (
        <p role="alert" className="error">
          Could not load the index list: {indices.error}
        </p>
      )}

      <Card
        {...tourAnchor(
          "strategy.setup",
          "Backtest setup",
          "Set the window and the trading line's rules before you run a backtest.",
        )}
      >
        <CardHeader>
          <CardTitle>Backtest setup</CardTitle>
          <CardDescription>
            Defaults sell one ~25-delta one-month index put per day, capped at 30 open, with light
            commission and slippage. Adjust the window and the line&apos;s rules, then run.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <AsyncBlock loading={indices.loading} error={indices.error}>
            <BacktestForm indexOptions={indexOptions} running={running} onRun={run} />
          </AsyncBlock>
        </CardContent>
      </Card>

      {error !== null && (
        <p role="alert" className="error">
          Backtest failed: {error}
        </p>
      )}

      {result !== null && (
        <ErrorBoundary label="Backtest results">
          <BacktestResults result={result} currency={currency} />
        </ErrorBoundary>
      )}

      {result === null && error === null && !running && (
        <div className="state-panel" role="status">
          No backtest run yet, configure the line above and press Run.
        </div>
      )}
    </Stack>
  );
}
