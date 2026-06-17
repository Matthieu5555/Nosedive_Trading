import { useMemo } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";

import type { FillsResponse, PositionsResponse } from "../../api";
import { AsyncBlock } from "../../components/AsyncBlock";
import { BookSummary } from "../../components/BookSummary";
import { ErrorBoundary } from "../../components/ErrorBoundary";
import { FillsLedger } from "../../components/FillsLedger";
import { PositionsTable } from "../../components/PositionsTable";
import { useFetch } from "../../hooks/useFetch";

// ② Le book — the composed/booked positions that are the *input* to the stress, folded in from
// the standalone Positions page (book summary $Greeks, open legs, fills ledger). Driven by the
// shared Basket underlying + trade date, so the book read and the stress share one context. The
// broker reconciliation is deliberately NOT folded here — it lives on Onglet 3 (post-orders).
export function LeBookSection({
  underlying,
  tradeDate,
  currency,
}: {
  underlying: string;
  tradeDate: string;
  currency: string;
}) {
  const query = useMemo(() => {
    const params = new URLSearchParams();
    if (underlying) params.set("underlying", underlying);
    if (tradeDate) params.set("trade_date", tradeDate);
    const suffix = params.toString();
    return suffix ? `?${suffix}` : "";
  }, [underlying, tradeDate]);

  const positions = useFetch<PositionsResponse>(underlying ? `/api/positions${query}` : "");
  const fills = useFetch<FillsResponse>(underlying ? `/api/positions/fills${query}` : "");

  return (
    <div className="basket-tab">
      <p className="basket-tab__lead">
        The book the stress acts on: its total market value and additive dollar Greeks, every open
        leg, and the append-only fills ledger it is accounted from. Booked from fills for the chosen
        underlying and date above.
      </p>

      <ErrorBoundary label="Book summary">
        <Card>
          <CardHeader>
            <CardTitle>Book summary</CardTitle>
            <CardDescription>
              The book&apos;s total market value and its additive dollar Greeks — summed across
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
              One row per live contract — quantity, mark, market value and the per-leg dollar Greeks.
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
                        Booked but unpriced legs ({positions.data.unpriced_contract_keys.length})
                      </h4>
                      <p>
                        These legs are booked from fills but have no banked pricing yet, so their
                        mark, market value and Greeks are zeroed — shown, never hidden.
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
              The append-only execution blotter — every booked fill with its venue timestamp. This is
              the source of record the book is accounted from.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <AsyncBlock loading={fills.loading} error={fills.error}>
              {fills.data && <FillsLedger fills={fills.data.fills ?? []} currency={currency} />}
            </AsyncBlock>
          </CardContent>
        </Card>
      </ErrorBoundary>
    </div>
  );
}
