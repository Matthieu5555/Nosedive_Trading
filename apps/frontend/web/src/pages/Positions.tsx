import { useEffect, useMemo, useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";
import { Label } from "@/ui/label";

import type {
  FillsResponse,
  IndicesResponse,
  PositionsResponse,
  RecordedDatesResponse,
} from "../api";
import { ApiError } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { BookSummary } from "../components/BookSummary";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { FillsLedger } from "../components/FillsLedger";
import { Cluster, Stack } from "../components/layout";
import { PositionsTable } from "../components/PositionsTable";
import { Reconciliation } from "../components/Reconciliation";
import { useReconciliation } from "../hooks/queries";
import { useFetch } from "../hooks/useFetch";
import { currencySymbol } from "../lib/format";
import { tourAnchor } from "../lib/tour";

const ALL_DATES = "";

export function PositionsPage() {
  const indices = useFetch<IndicesResponse>("/api/indices");
  const indexOptions = useMemo(() => indices.data?.indices ?? [], [indices.data]);

  const [index, setIndex] = useState("");
  useEffect(() => {
    if (indexOptions.length === 0) return;
    if (!index || !indexOptions.some((o) => o.symbol === index)) {
      setIndex(indexOptions[0].symbol);
    }
  }, [indexOptions, index]);

  const [tradeDate, setTradeDate] = useState<string>(ALL_DATES);

  const recorded = useFetch<RecordedDatesResponse>(
    index ? `/api/recorded-dates?index=${encodeURIComponent(index)}` : "",
  );
  const dateOptions = useMemo(() => recorded.data?.dates ?? [], [recorded.data]);
  useEffect(() => {
    if (tradeDate !== ALL_DATES && !dateOptions.includes(tradeDate)) {
      setTradeDate(ALL_DATES);
    }
  }, [dateOptions, tradeDate]);

  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (index) params.set("underlying", index);
    if (tradeDate) params.set("trade_date", tradeDate);
    const suffix = params.toString();
    return suffix ? `?${suffix}` : "";
  }, [index, tradeDate]);

  const positions = useFetch<PositionsResponse>(index ? `/api/positions${query}` : "");
  const fills = useFetch<FillsResponse>(index ? `/api/positions/fills${query}` : "");

  const currency = currencySymbol(indexOptions.find((o) => o.symbol === index)?.currency);

  // Broker reconciliation is account-wide (all underlyings, keyed to the broker account), not scoped
  // by the per-underlying selector above. It is an integrity check on the real book, broker snapshot
  // vs our fills-based book, so it lives next to the book it checks. Empty account = latest captured.
  const [account, setAccount] = useState<string>("");
  const reconciliation = useReconciliation(account);

  return (
    <Stack as="section" className="page" gap="md">
      <div className="page-header">
        <div>
          <p className="eyebrow">What I own, what it&apos;s worth, what my risk is</p>
          <h1>Positions</h1>
        </div>
        <Cluster className="control-row" gap="sm">
          <select
            aria-label="Underlying"
            {...tourAnchor(
              "positions.underlying",
              "Underlying picker",
              "Choose which underlying's open positions and book summary to show.",
            )}
            value={index}
            disabled={indexOptions.length === 0}
            onChange={(event) => setIndex(event.target.value)}
          >
            {indexOptions.map((item) => (
              <option key={item.symbol} value={item.symbol}>
                {item.name} ({item.symbol})
              </option>
            ))}
          </select>
          <select
            aria-label="Trade date"
            value={tradeDate}
            onChange={(event) => setTradeDate(event.target.value)}
          >
            <option value={ALL_DATES}>All booked dates</option>
            {dateOptions.map((date) => (
              <option key={date} value={date}>
                {date}
              </option>
            ))}
          </select>
        </Cluster>
      </div>

      <AsyncBlock loading={indices.loading} error={indices.error}>
        <Stack gap="md">
          <ErrorBoundary label="Book summary">
            <Card>
              <CardHeader>
                <CardTitle>Book summary</CardTitle>
                <CardDescription>
                  The book&apos;s total market value and its additive dollar Greeks, summed across
                  priced legs, accounted from booked fills.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <AsyncBlock loading={positions.loading} error={positions.error}>
                  {positions.data?.book && (
                    <BookSummary book={positions.data.book} currency={currency} />
                  )}
                </AsyncBlock>
              </CardContent>
            </Card>
          </ErrorBoundary>

          <ErrorBoundary label="Open positions">
            <Card>
              <CardHeader>
                <CardTitle>Open positions</CardTitle>
                <CardDescription>
                  One row per live contract, quantity, mark, market value and the per-leg dollar
                  Greeks.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <AsyncBlock loading={positions.loading} error={positions.error}>
                  {positions.data && (
                    <>
                      <PositionsTable lines={positions.data.lines ?? []} currency={currency} />
                      {(positions.data.unpriced_contract_keys?.length ?? 0) > 0 && (
                        <div role="alert" className="gaps" aria-label="unpriced legs">
                          <h4>
                            Booked but unpriced legs ({positions.data.unpriced_contract_keys.length}
                            )
                          </h4>
                          <p>
                            These legs are booked from fills but have no banked pricing yet, so
                            their mark, market value and Greeks are zeroed, shown, never hidden.
                          </p>
                          <ul>
                            {positions.data.unpriced_contract_keys.map((key) => (
                              <li key={key}>{key}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </>
                  )}
                </AsyncBlock>
              </CardContent>
            </Card>
          </ErrorBoundary>

          <ErrorBoundary label="Fills ledger">
            <Card>
              <CardHeader>
                <CardTitle>Fills ledger</CardTitle>
                <CardDescription>
                  The append-only execution blotter, every booked fill with its venue timestamp.
                  This is the source of record the book is accounted from.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <AsyncBlock loading={fills.loading} error={fills.error}>
                  {fills.data && <FillsLedger fills={fills.data.fills ?? []} currency={currency} />}
                </AsyncBlock>
              </CardContent>
            </Card>
          </ErrorBoundary>

          {/* Account-wide integrity check, separate from the per-underlying book above. Labelled
              "across the whole account" because it spans every underlying, not the one selected. */}
          <ErrorBoundary label="Broker reconciliation">
            <Card
              {...tourAnchor(
                "positions.reconciliation",
                "Broker reconciliation",
                "Checks the broker's account against our fills-based book, across every underlying.",
              )}
            >
              <CardHeader>
                <CardTitle>Broker reconciliation</CardTitle>
                <CardDescription>
                  Does the broker&apos;s account agree with our fills-based book? Per-status counts
                  (match / break / broker-only / book-only) and the break lines, across the whole
                  account, every underlying, not just the one selected above.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <Stack gap="sm">
                  <Cluster gap="sm" align="end">
                    <Stack gap="3xs" align="flex-start">
                      <Label htmlFor="recon-account">Broker account</Label>
                      <input
                        id="recon-account"
                        aria-label="Broker account"
                        placeholder="latest captured"
                        value={account}
                        onChange={(event) => setAccount(event.target.value)}
                      />
                    </Stack>
                  </Cluster>
                  <AsyncBlock
                    loading={reconciliation.isPending}
                    error={reconciliationError(
                      reconciliation.isError ? reconciliation.error : null,
                    )}
                  >
                    {reconciliation.data && <Reconciliation report={reconciliation.data} />}
                    {noBrokerAccount(reconciliation.isError ? reconciliation.error : null) && (
                      <article className="panel" aria-label="Broker reconciliation (no account)">
                        <p role="status">
                          No broker account snapshot has been captured yet, nothing to reconcile
                          against.
                        </p>
                      </article>
                    )}
                  </AsyncBlock>
                </Stack>
              </CardContent>
            </Card>
          </ErrorBoundary>
        </Stack>
      </AsyncBlock>
    </Stack>
  );
}

function noBrokerAccount(error: Error | null): boolean {
  return error instanceof ApiError && error.status === 400;
}

function reconciliationError(error: Error | null): string | null {
  if (error === null) return null;
  if (noBrokerAccount(error)) return null;
  return error.message;
}
