import { useState } from "react";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/ui/card";
import { Label } from "@/ui/label";

import { AsyncBlock } from "../components/AsyncBlock";
import { StressSurface } from "../components/StressSurface";
import { usePortfolios, useRiskScenarios } from "../hooks/queries";
import type { ScenariosResponse } from "../stressApi";

export function RiskScenariosPage() {
  const [portfolio, setPortfolio] = useState<string>("");
  const portfolios = usePortfolios();
  const scenarios = useRiskScenarios(portfolio);

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Scenario engine</p>
          <h1>Risk Scenarios</h1>
        </div>
        <div className="control-row flex flex-col items-start gap-1">
          <Label htmlFor="risk-portfolio">Portfolio</Label>
          <select
            id="risk-portfolio"
            aria-label="Portfolio"
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
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Persisted scenario surface</CardTitle>
          <CardDescription>
            The cron-written ±spot × ±vol surface per configured portfolio. Empty until a portfolio
            is configured and a run lands — to stress a basket on demand, compose it on the Basket
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
    </section>
  );
}

function ScenarioBoard({ data }: { data: ScenariosResponse }) {
  return (
    <div className="risk-grid">
      <StressSurface
        surface={data.surface}
        kicker={data.portfolio_id ?? "All portfolios"}
        emptyMessage="No stress surface persisted yet for this selection."
      />
    </div>
  );
}
