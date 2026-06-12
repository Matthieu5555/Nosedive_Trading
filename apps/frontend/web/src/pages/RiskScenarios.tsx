// Tab 2 — Risk Scenarios (roadmap 2B), wearing Antho's panel grammar.
//
// The *persisted* stress view: the cron-written full-reprice surface per configured portfolio
// (ADR 0006), read back from GET /api/risk/scenarios and reshaped by the BFF — empty until a
// portfolio is configured and a run lands. Rendered through the shared StressSurface (3D Plotly
// surface + heatmap, ADR 0030). The *on-demand* counterpart (compose a basket, POST
// /api/basket/scenarios) lives on the Basket tab beside pricing — it is deliberately NOT
// duplicated here (owner report 2026-06-12: the two tabs had become near-copies).

import { useState } from "react";

import { AsyncBlock } from "../components/AsyncBlock";
import { StressSurface } from "../components/StressSurface";
import { useFetch } from "../hooks/useFetch";
import type { ScenariosResponse } from "../stressApi";

interface PortfoliosResponse {
  portfolios: string[];
}

export function RiskScenariosPage() {
  const [portfolio, setPortfolio] = useState<string>("");
  const portfolios = useFetch<PortfoliosResponse>("/api/risk/portfolios");
  const query = portfolio ? `?portfolio_id=${encodeURIComponent(portfolio)}` : "";
  const scenarios = useFetch<ScenariosResponse>(`/api/risk/scenarios${query}`);

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Scenario engine</p>
          <h1>Risk Scenarios</h1>
        </div>
        <div className="control-row">
          <select
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

      <div className="page-subheader">
        <h2>Persisted scenario surface</h2>
        <p className="muted">
          The cron-written ±spot × ±vol surface per configured portfolio. Empty until a portfolio
          is configured and a run lands — to stress a basket on demand, compose it on the Basket
          tab and use “Stress basket”.
        </p>
      </div>
      <AsyncBlock loading={scenarios.loading} error={scenarios.error}>
        {scenarios.data && <ScenarioBoard data={scenarios.data} />}
      </AsyncBlock>
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
