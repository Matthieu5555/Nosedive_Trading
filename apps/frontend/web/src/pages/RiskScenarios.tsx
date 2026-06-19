import { useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";
import { Label } from "@/ui/label";

import { ApiError } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { AttributionWaterfall } from "../components/AttributionWaterfall";
import { Cluster, Grid, Stack } from "../components/layout";
import { NamedScenarios } from "../components/NamedScenarios";
import { Reconciliation } from "../components/Reconciliation";
import { RateSweep, StressSurface } from "../components/StressSurface";
import {
  useBookAttribution,
  usePortfolios,
  useReconciliation,
  useRiskScenarios,
} from "../hooks/queries";
import { tourAnchor } from "../lib/tour";
import type { ScenariosResponse } from "../stressApi";

export function RiskScenariosPage() {
  const [portfolio, setPortfolio] = useState<string>("");
  const [account, setAccount] = useState<string>("");

  const portfolios = usePortfolios();
  const scenarios = useRiskScenarios(portfolio);
  const attribution = useBookAttribution(portfolio);
  const reconciliation = useReconciliation(account);

  // The selection that scopes the attribution. The card's title ("Where the P&L came from") already
  // titles the section, so we pass the bare selection as the waterfall's kicker rather than letting
  // it restate a heading, keeping one title per panel.
  const attributionScope = portfolio || "All portfolios";

  return (
    <Stack as="section" className="page" gap="md">
      <div className="page-header">
        <div>
          <p className="eyebrow">How does the book hold up, and does it match the broker?</p>
          <h1>Risk Scenarios</h1>
        </div>
        <Cluster gap="sm" align="end">
          <Stack gap="3xs" align="flex-start">
            <Label htmlFor="risk-portfolio">Portfolio</Label>
            <select
              id="risk-portfolio"
              aria-label="Portfolio"
              {...tourAnchor(
                "risk.portfolio",
                "Portfolio picker",
                "Choose which portfolio to scope the scenarios and attribution to.",
              )}
              value={portfolio}
              onChange={(event) => setPortfolio(event.target.value)}
            >
              <option value="">All portfolios</option>
              {(portfolios.data?.portfolios ?? []).map((id) => (
                <option key={id} value={id}>
                  {id}
                </option>
              ))}
            </select>
            {portfolios.isError && (
              <p role="alert" className="error">
                Could not load the portfolio list: {portfolios.error.message}
              </p>
            )}
          </Stack>
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
      </div>

      <Grid min="420px" gap="md">
        <Card
          {...tourAnchor(
            "risk.scenarios",
            "Named scenarios",
            "Replay labelled crises like 2008 and COVID-2020 against today's book.",
          )}
        >
          <CardHeader>
            <CardTitle>Named historical scenarios</CardTitle>
            <CardDescription>
              Replay labelled crises (2008, COVID-2020, …) against today&apos;s book, one compound
              spot/vol/rate shock each, full-repriced. Empty until a scenario catalogue is
              configured.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <AsyncBlock
              loading={scenarios.isPending}
              error={scenarios.isError ? scenarios.error.message : null}
            >
              {scenarios.data && <NamedScenarios scenarios={scenarios.data.named ?? []} />}
            </AsyncBlock>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Where the P&amp;L came from</CardTitle>
            <CardDescription>
              The book&apos;s realized/scenario P&amp;L split by Greek, with the leftover residual
              shown as its own honesty bar. Empty until a scenario attribution lands for this
              selection.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <AsyncBlock
              loading={attribution.isPending}
              error={attribution.isError ? attribution.error.message : null}
              height={180}
              subject="the P&L attribution"
            >
              {attribution.data && (
                <AttributionWaterfall
                  attribution={attribution.data}
                  kicker={attributionScope}
                  embedded
                />
              )}
            </AsyncBlock>
          </CardContent>
        </Card>
      </Grid>

      <Card>
        <CardHeader>
          <CardTitle>Broker reconciliation</CardTitle>
          <CardDescription>
            Does the broker&apos;s account agree with our fills-based book? Per-status counts (match
            / break / broker-only / book-only) and the break lines.
          </CardDescription>
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
                  No broker account snapshot has been captured yet, nothing to reconcile against.
                </p>
              </article>
            )}
          </AsyncBlock>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Persisted scenario surface</CardTitle>
          <CardDescription>
            The cron-written ±spot × ±vol surface per configured portfolio. Empty until a portfolio
            is configured and a run lands, to stress a basket on demand, compose it on the Basket
            tab and use “Stress basket”.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <AsyncBlock
            loading={scenarios.isPending}
            error={scenarios.isError ? scenarios.error.message : null}
          >
            {scenarios.data && <ScenarioBoard data={scenarios.data} />}
          </AsyncBlock>
        </CardContent>
      </Card>
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

function ScenarioBoard({ data }: { data: ScenariosResponse }) {
  // Owner: "the element should take the entire subspace or should be centered at the very least."
  // StressSurface emits four panels in order: the narrow text summary, the wide heatmap, the wide 3D
  // surface, then (here) the narrow rate sweep. In an auto-fit grid the two wide charts already span
  // every column (`grid-column: 1 / -1`), but because they sit BETWEEN the two narrow panels the
  // summary and the rate sweep can never pair on one row, so each landed alone in a single track and
  // left the rest of the row empty, the "cramped left, big void right" complaint. A plain vertical
  // Stack makes every panel full-width, so each one fills the whole container and nothing is crammed.
  return (
    <Stack gap="md">
      <StressSurface
        surface={data.surface}
        kicker={data.portfolio_id ?? "All portfolios"}
        emptyMessage="No stress surface persisted yet for this selection."
      />
      {data.rate && data.rate.length > 0 && <RateSweep rates={data.rate} />}
    </Stack>
  );
}
