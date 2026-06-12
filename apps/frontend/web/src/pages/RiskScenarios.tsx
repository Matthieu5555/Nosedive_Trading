// Tab 2 — Risk Scenarios (roadmap 2B), wearing Antho's panel grammar.
//
// The stress engine is read-only at the seam: the cron writes the full-reprice ±spot × ±vol
// grid into scenario_results, the BFF only reshapes it (ADR 0006/0034). So this tab has no
// interactive shock form (Antho's demo POSTed a custom shock — that has no backend here); it
// reads back GET /api/risk/scenarios and renders the persisted PnL surface as a 3D Plotly
// surface plus a heatmap (ADR 0030), with a read-only portfolio selector. Every cell is a full
// reprice, so the surface diverges from a local Greek approximation at large shocks by design.

import { useState } from "react";

import type { Data } from "plotly.js";

import { AsyncBlock } from "../components/AsyncBlock";
import { Metric } from "../components/Metric";
import { Plot } from "../components/Plot";
import { useFetch } from "../hooks/useFetch";
import { signedMoney } from "../lib/format";
import type { ScenariosResponse } from "../stressApi";

interface PortfoliosResponse {
  portfolios: string[];
}

const SURFACE_LABEL =
  "Stress PnL surface — full reprice over spot shock (relative) × vol shock (additive)";
const HEATMAP_LABEL = "Stress PnL heatmap — spot shock × vol shock";

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

      <AsyncBlock loading={scenarios.loading} error={scenarios.error}>
        {scenarios.data && <ScenarioBoard data={scenarios.data} />}
      </AsyncBlock>
    </section>
  );
}

function ScenarioBoard({ data }: { data: ScenariosResponse }) {
  const surface = data.surface;
  if (surface.n_cells === 0) {
    return (
      <article className="panel">
        <p>No stress surface persisted yet for this selection.</p>
      </article>
    );
  }

  // A null cell is a labelled hole (no persisted scenario for that shock pair), excluded
  // from the summary stats; Plotly renders the null as a gap in the heatmap/surface.
  const flat = surface.scenario_pnl.flat().filter((value): value is number => value !== null);
  const maxGain = Math.max(...flat);
  const maxLoss = Math.min(...flat);

  // Plotly z is spot-major: z[i][j] is the PnL at spot_shock[i] (y axis) and vol_shock[j]
  // (x axis), matching the BFF z-grid orientation.
  const surfaceTrace: Data = {
    type: "surface",
    x: surface.vol_shock,
    y: surface.spot_shock,
    z: surface.scenario_pnl,
    name: "stress PnL",
  };
  const heatmapTrace: Data = {
    type: "heatmap",
    x: surface.vol_shock,
    y: surface.spot_shock,
    z: surface.scenario_pnl,
    colorscale: "RdYlGn",
    name: "stress PnL",
  };

  return (
    <div className="risk-grid">
      <article className="panel scenario-summary">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{data.portfolio_id ?? "All portfolios"}</p>
            <h2>Stress summary</h2>
          </div>
          <span className={maxLoss < 0 ? "status negative" : "status"}>
            {surface.n_cells} cells
            {surface.has_holes ? ` — ${surface.n_holes} missing` : ""}
          </span>
        </div>
        <div className="quote-strip">
          <Metric label="Max gain" value={signedMoney(maxGain)} />
          <Metric label="Max loss" value={signedMoney(maxLoss)} />
          <Metric label="Spot points" value={String(surface.spot_shock.length)} />
          <Metric label="Vol points" value={String(surface.vol_shock.length)} />
          <Metric label="Version" value={surface.scenario_version ?? "—"} />
        </div>
        <p>
          Spot shock is relative (new spot = spot × (1 + s)); vol shock is additive (new vol =
          vol + v). The centre cell (0, 0) is ≈ 0 PnL by construction. PnL unit:{" "}
          <strong>{surface.unit}</strong>.
        </p>
      </article>

      <article className="panel heatmap-panel">
        <div className="panel-heading">
          <h2>PnL heatmap</h2>
          <span className="status">spot × vol</span>
        </div>
        <Plot
          label={HEATMAP_LABEL}
          data={[heatmapTrace]}
          layout={{
            xaxis: { title: { text: "vol shock (additive, vol pts)" } },
            yaxis: { title: { text: "spot shock (relative)" } },
          }}
        />
      </article>

      <article className="panel surface-panel">
        <div className="panel-heading">
          <h2>PnL surface</h2>
          <span className="status">full reprice</span>
        </div>
        <Plot
          label={SURFACE_LABEL}
          data={[surfaceTrace]}
          layout={{
            scene: {
              xaxis: { title: { text: "vol shock (additive, vol pts)" } },
              yaxis: { title: { text: "spot shock (relative)" } },
              zaxis: { title: { text: `PnL (${surface.unit})` } },
            },
          }}
        />
      </article>
    </div>
  );
}
