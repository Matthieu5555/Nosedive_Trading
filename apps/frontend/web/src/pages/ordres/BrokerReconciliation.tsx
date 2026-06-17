import { useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";
import { Label } from "@/ui/label";

import { ApiError } from "../../api";
import { AsyncBlock } from "../../components/AsyncBlock";
import { Reconciliation } from "../../components/Reconciliation";
import { useReconciliation } from "../../hooks/queries";

function noBrokerAccount(error: Error | null): boolean {
  return error instanceof ApiError && error.status === 400;
}

function reconciliationError(error: Error | null): string | null {
  if (error === null) return null;
  if (noBrokerAccount(error)) return null;
  return error.message;
}

export function BrokerReconciliation() {
  const [account, setAccount] = useState<string>("");
  const reconciliation = useReconciliation(account);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Broker reconciliation</CardTitle>
        <CardDescription>
          After the orders are placed, does the broker&apos;s account agree with our fills-based
          book? Per-status counts (match / break / broker-only / book-only) and the break lines.
        </CardDescription>
        <div className="control-row">
          <div className="flex flex-col items-start gap-1">
            <Label htmlFor="recon-account">Broker account</Label>
            <input
              id="recon-account"
              aria-label="Broker account"
              placeholder="latest captured"
              value={account}
              onChange={(event) => setAccount(event.target.value)}
            />
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <AsyncBlock
          loading={reconciliation.isPending}
          error={reconciliationError(reconciliation.isError ? reconciliation.error : null)}
        >
          {reconciliation.data && <Reconciliation report={reconciliation.data} />}
          {noBrokerAccount(reconciliation.isError ? reconciliation.error : null) && (
            <article className="panel" aria-label="Broker reconciliation (no account)">
              <p role="status">
                No broker account snapshot has been captured yet — nothing to reconcile against.
              </p>
            </article>
          )}
        </AsyncBlock>
      </CardContent>
    </Card>
  );
}
