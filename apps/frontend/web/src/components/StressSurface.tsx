// The stress-PnL surface panels (roadmap 2B), shared by the Risk Scenarios tab (persisted cron
// surface) and the Basket Builder (on-demand full reprice). Given a reshaped (spot × vol) surface
// it renders a summary strip, a heatmap, and a 3-D Plotly surface — the same grammar in both
// places, so the two views can never drift in look or labelling.

import type { Data } from "plotly.js";

import type { StressSurfaceData } from "../stressApi";
import { Metric } from "./Metric";
import { Plot } from "./Plot";
import { sciUnit, withCurrency } from "../lib/format";

const SURFACE_LABEL =
  "Stress PnL surface — full reprice over spot shock (relative) × vol shock (additive)";
const HEATMAP_LABEL = "Stress PnL heatmap — spot shock × vol shock";

// The finite (non-hole) PnL cells, so max/min ignore labelled holes rather than reading a hole
// as a 0 quote.
function finiteCells(grid: (number | null)[][]): number[] {
  return grid.flat().filter((value): value is number => value !== null);
}

export function StressSurface({
  surface,
  kicker,
  emptyMessage = "No stress surface for this selection.",
  currency,
}: {
  surface: StressSurfaceData;
  kicker: string;
  emptyMessage?: string;
  // The currency symbol the monetized PnL unit should render in (the index quote currency). When
  // omitted (e.g. the Risk Scenarios tab), the unit renders verbatim — exactly as it did before.
  currency?: string;
}) {
  if (surface.n_cells === 0) {
    return (
      <article className="panel">
        <p>{emptyMessage}</p>
      </article>
    );
  }

  // The PnL unit carries `$` as the currency placeholder (a legacy contract artifact); render it
  // in the real currency when one was threaded, otherwise leave the unit untouched.
  const unit = withCurrency(surface.unit, currency) ?? surface.unit;

  const finite = finiteCells(surface.scenario_pnl);
  const maxGain = finite.length === 0 ? 0 : Math.max(...finite);
  const maxLoss = finite.length === 0 ? 0 : Math.min(...finite);

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
    <>
      <article className="panel scenario-summary">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{kicker}</p>
            <h2>Stress summary</h2>
          </div>
          <span className={maxLoss < 0 ? "status negative" : "status"}>
            {surface.n_cells} cells
            {surface.has_holes ? ` — ${surface.n_holes} missing` : ""}
          </span>
        </div>
        <div className="quote-strip">
          <Metric label="Max gain" value={sciUnit(maxGain, unit)} />
          <Metric label="Max loss" value={sciUnit(maxLoss, unit)} />
          <Metric label="Spot points" value={String(surface.spot_shock.length)} />
          <Metric label="Vol points" value={String(surface.vol_shock.length)} />
          <Metric label="Version" value={surface.scenario_version ?? "—"} />
        </div>
        <p>
          Spot shock is relative (new spot = spot × (1 + s)); vol shock is additive (new vol = vol
          + v). The centre cell (0, 0) is ≈ 0 PnL by construction. PnL unit:{" "}
          <strong>{unit}</strong>.
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
              zaxis: { title: { text: `PnL (${unit})` } },
            },
          }}
        />
      </article>
    </>
  );
}
