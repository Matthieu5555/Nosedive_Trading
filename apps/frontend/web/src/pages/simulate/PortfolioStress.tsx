import { useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";
import { Label } from "@/ui/label";

import { AsyncBlock } from "../../components/AsyncBlock";
import { AttributionWaterfall } from "../../components/AttributionWaterfall";
import { Cluster, Stack } from "../../components/layout";
import { NamedScenarios } from "../../components/NamedScenarios";
import { RateSweep, StressSurface } from "../../components/StressSurface";
import { useBookAttribution, usePortfolios, useRiskScenarios } from "../../hooks/queries";
import { tourAnchor } from "../../lib/tour";
import type { ScenariosResponse } from "../../stressApi";

// Simulate / "My book" mode: stress the real, held portfolio. The by-Greek attribution ("where the
// P&L came from") leads the view, front and centre, full-width; the named historical crises and the
// persisted ±spot × ±vol surface follow. All read the cron-written Risk path for the chosen
// portfolio. Reconciliation does NOT live here, it is an integrity check on the real book and now
// sits on the Positions page; this view is purely the what-if on what you hold.
export function PortfolioStress() {
  const [portfolio, setPortfolio] = useState<string>("");

  const portfolios = usePortfolios();
  const scenarios = useRiskScenarios(portfolio);
  const attribution = useBookAttribution(portfolio);

  // The selection that scopes the attribution. The card's title ("Where the P&L came from") already
  // titles the section, so we pass the bare selection as the waterfall's kicker rather than letting
  // it restate a heading, keeping one title per panel.
  const attributionScope = portfolio || "All portfolios";

  return (
    <Stack gap="md">
      <Cluster gap="sm" align="end">
        <Stack gap="3xs" align="flex-start">
          <Label htmlFor="simulate-portfolio">Portfolio</Label>
          <select
            id="simulate-portfolio"
            aria-label="Portfolio"
            {...tourAnchor(
              "simulate.portfolio",
              "Portfolio picker",
              "Choose which held portfolio to scope the scenarios and attribution to.",
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
      </Cluster>

      <Card>
        <CardHeader>
          <CardTitle>Where the P&amp;L came from</CardTitle>
          <CardDescription>
            The book&apos;s realized/scenario P&amp;L split by Greek, with the leftover residual
            shown as its own honesty bar. This leads the view: it is the first thing to read off the
            book you hold. Empty until a scenario attribution lands for this selection.
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

      <Card
        {...tourAnchor(
          "simulate.scenarios",
          "Named scenarios",
          "Replay labelled crises like 2008 and COVID-2020 against the book you hold.",
        )}
      >
        <CardHeader>
          <CardTitle>Named historical scenarios</CardTitle>
          <CardDescription>
            Replay labelled crises (2008, COVID-2020, …) against the book you hold, one compound
            spot/vol/rate shock each, full-repriced. Empty until a scenario catalogue is configured.
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
          <CardTitle>Persisted scenario surface</CardTitle>
          <CardDescription>
            The cron-written ±spot × ±vol surface per configured portfolio. Empty until a portfolio
            is configured and a run lands. To stress a basket you build on the spot instead, switch
            the book source above to “Build a basket”.
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
