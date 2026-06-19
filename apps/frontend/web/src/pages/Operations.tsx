import { useEffect, useMemo, useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/ui/card";
import { Label } from "@/ui/label";

import type { IndicesResponse } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { ErrorBoundary } from "../components/ErrorBoundary";
import { InfoDot } from "../components/InfoDot";
import { Cluster, Stack } from "../components/layout";
import { FreshnessPanel } from "../components/operations/FreshnessPanel";
import { IbkrConnectionPanel } from "../components/operations/IbkrConnectionPanel";
import { RunControlPanel } from "../components/operations/RunControlPanel";
import { SystemHealthPanel } from "../components/operations/SystemHealthPanel";
import { useHealth, useRecordedDates } from "../hooks/queries";
import { useFetch } from "../hooks/useFetch";
import { tourAnchor } from "../lib/tour";

export function OperationsPage() {
  const health = useHealth();
  const indices = useFetch<IndicesResponse>("/api/indices");
  const indexOptions = useMemo(() => indices.data?.indices ?? [], [indices.data]);

  const [index, setIndex] = useState("");
  useEffect(() => {
    if (indexOptions.length === 0) return;
    if (!index || !indexOptions.some((o) => o.symbol === index)) {
      setIndex(indexOptions[0].symbol);
    }
  }, [indexOptions, index]);

  const recorded = useRecordedDates(index);

  return (
    <Stack as="section" className="page" gap="md">
      <div className="page-header">
        <div>
          <p className="eyebrow">
            Is the system healthy, is today&rsquo;s data in, when did we last compute risk?
          </p>
          <h1>Operations</h1>
        </div>
      </div>

      <Card
        {...tourAnchor(
          "operations.health",
          "System health panel",
          "One glance at whether services are up and today's data and risk all completed.",
        )}
      >
        <CardHeader>
          <Cluster gap="2xs" align="center">
            <CardTitle>System health</CardTitle>
            <InfoDot
              label="System health, what it covers"
              body="One glance: are services up, is market data flowing, and did the surfaces, quality control and stress scenarios all complete for the latest day?"
            />
          </Cluster>
        </CardHeader>
        <CardContent>
          <ErrorBoundary label="System health">
            <AsyncBlock
              loading={health.isPending}
              error={health.isError ? health.error.message : null}
            >
              {health.data && <SystemHealthPanel health={health.data} />}
            </AsyncBlock>
          </ErrorBoundary>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <Cluster gap="2xs" align="center">
            <CardTitle>Run control</CardTitle>
            <InfoDot
              label="Run control, how it works"
              body="Launch a capture run and watch it land. Pick a provider and underlying, launch, and the job list below tracks each run from queued to done."
            />
          </Cluster>
        </CardHeader>
        <CardContent>
          <ErrorBoundary label="Run control">
            <RunControlPanel />
          </ErrorBoundary>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <Cluster gap="2xs" align="center">
            <CardTitle>IBKR connection</CardTitle>
            <InfoDot
              label="IBKR connection, what it shows"
              body="The live IBKR Client-Portal session behind a real run. See the honest gateway state, open the brokerage session once authenticated, and refresh on demand. Logging in itself runs from a shell, not the web app."
            />
          </Cluster>
        </CardHeader>
        <CardContent>
          <ErrorBoundary label="IBKR connection">
            <IbkrConnectionPanel />
          </ErrorBoundary>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <Cluster gap="2xs" align="center">
            <CardTitle>Risk &amp; analytics freshness</CardTitle>
            <InfoDot
              label="Freshness, what it reports"
              body="When risk and analytics last computed, and how many clean days are on record."
            />
          </Cluster>
          <Cluster gap="sm" align="end">
            <div className="control-field">
              <Label htmlFor="ops-index">Index</Label>
              <select
                id="ops-index"
                aria-label="Index"
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
            </div>
          </Cluster>
        </CardHeader>
        <CardContent>
          <ErrorBoundary label="Freshness">
            <AsyncBlock
              loading={indices.loading || recorded.isPending}
              error={indices.error ?? (recorded.isError ? recorded.error.message : null)}
            >
              {recorded.data && <FreshnessPanel recorded={recorded.data} />}
            </AsyncBlock>
          </ErrorBoundary>
        </CardContent>
      </Card>
    </Stack>
  );
}
