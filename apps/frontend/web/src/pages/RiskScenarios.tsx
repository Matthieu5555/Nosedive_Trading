// Tab 2 — Risk Scenarios (roadmap 2B), wearing Antho's panel grammar.
//
// The *persisted* stress view: the cron-written full-reprice surface per configured portfolio
// (ADR 0006), read back from GET /api/risk/scenarios and reshaped by the BFF — empty until a
// portfolio is configured and a run lands. Rendered through the shared StressSurface (3D Plotly
// surface + heatmap, ADR 0030). The *on-demand* counterpart (compose a basket, POST
// /api/basket/scenarios) lives on the Basket tab beside pricing — it is deliberately NOT
// duplicated here (owner report 2026-06-12: the two tabs had become near-copies).

// Reference migration (phase-2 hardening): this page is the canonical example contributors copy
// when reaching for the new shadcn primitives. It composes the `Card` family from src/ui over
// the existing `.page` panel grammar — Tailwind utilities and legacy CSS coexist on the same
// page. Note the native <select> is kept on purpose: the e2e/test selectors and the dark-theme
// styling already work, and a Radix Select would change the accessibility tree for no gain here.
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
          {/* The portfolio list failing used to be silent — the dropdown just showed "All
              portfolios" with no hint the real list never arrived. Say so. */}
          {portfolios.isError && (
            <p role="alert" className="error">
              Could not load the portfolio list: {portfolios.error.message}
            </p>
          )}
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
