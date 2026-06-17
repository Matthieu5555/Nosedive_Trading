import type { Data } from "plotly.js";

import { Scroll, Stack } from "../components/layout";
import { sci, sciUnit, withCurrency } from "../lib/format";
import type { RateScenario, StressSurfaceData } from "../stressApi";
import { Metric } from "./Metric";
import { Plot } from "./Plot";

const SURFACE_LABEL =
  "Stress PnL surface, full reprice over spot shock (relative) × vol shock (additive)";
const HEATMAP_LABEL = "Stress PnL heatmap, spot shock × vol shock";

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

  currency?: string;
}) {
  if (surface.n_cells === 0) {
    return (
      <article className="panel">
        <p>{emptyMessage}</p>
      </article>
    );
  }

  const unit = withCurrency(surface.unit, currency) ?? surface.unit;

  const finite = finiteCells(surface.scenario_pnl);
  const maxGain = finite.length === 0 ? 0 : Math.max(...finite);
  const maxLoss = finite.length === 0 ? 0 : Math.min(...finite);

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
        <Stack gap="md">
          <div className="panel-heading">
            <div>
              <p className="panel-kicker">{kicker}</p>
              <h2>Stress summary</h2>
            </div>
            <span className={maxLoss < 0 ? "status negative" : "status"}>
              {surface.n_cells} cells
              {surface.has_holes ? `, ${surface.n_holes} missing` : ""}
            </span>
          </div>
          <div className="quote-strip">
            <Metric label="Max gain" value={sciUnit(maxGain, unit)} />
            <Metric label="Max loss" value={sciUnit(maxLoss, unit)} />
            <Metric label="Spot points" value={String(surface.spot_shock.length)} />
            <Metric label="Vol points" value={String(surface.vol_shock.length)} />
            <Metric label="Version" value={surface.scenario_version ?? "-"} />
          </div>
          <p>
            Spot shock is relative (new spot = spot × (1 + s)); vol shock is additive (new vol = vol
            + v). The centre cell (0, 0) is ≈ 0 PnL by construction. PnL unit:{" "}
            <strong>{unit}</strong>.
          </p>
        </Stack>
      </article>

      <article className="panel heatmap-panel">
        <Stack gap="md">
          <div className="panel-heading">
            <h2>PnL heatmap</h2>
            <span className="status">spot × vol</span>
          </div>
          <Scroll>
            <Plot
              label={HEATMAP_LABEL}
              data={[heatmapTrace]}
              layout={{
                xaxis: { title: { text: "vol shock (additive, vol pts)" } },
                yaxis: { title: { text: "spot shock (relative)" } },
              }}
            />
          </Scroll>
        </Stack>
      </article>

      <article className="panel surface-panel">
        <Stack gap="md">
          <div className="panel-heading">
            <h2>PnL surface</h2>
            <span className="status">full reprice</span>
          </div>
          <Scroll>
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
          </Scroll>
        </Stack>
      </article>
    </>
  );
}

export function RateSweep({
  rates,
  currency,
  emptyMessage = "No rate-shock sweep is configured for this selection.",
}: {
  rates: RateScenario[];
  currency?: string;
  emptyMessage?: string;
}) {
  if (rates.length === 0) {
    return (
      <article className="panel" aria-label="Rate-shock sweep (empty)">
        <p role="status">{emptyMessage}</p>
      </article>
    );
  }

  const ordered = [...rates].sort((a, b) => a.rate_shock - b.rate_shock);
  const worst = ordered.reduce(
    (lowest, rate) => (rate.scenario_pnl < lowest.scenario_pnl ? rate : lowest),
    ordered[0],
  );
  const worstUnit = withCurrency(worst.unit, currency) ?? worst.unit;

  return (
    <article className="panel rate-sweep" aria-label="Rate-shock sweep">
      <Stack gap="md">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">Rates ±bp</p>
            <h2>Rate-shock sweep</h2>
          </div>
          <span className={worst.scenario_pnl < 0 ? "status negative" : "status"}>
            {sciUnit(worst.scenario_pnl, worstUnit)}
          </span>
        </div>
        <p>
          A parallel shift of the rate curve (additive, forward-fixed), full-repriced beside the
          spot × vol surface, swept on its own axis, not crossed with it. Each row is one shock in
          basis points and the book&apos;s P&amp;L delta. P&amp;L unit: <strong>{worstUnit}</strong>
          .
        </p>
        <Scroll>
          <table aria-label="Rate-shock sweep">
            <thead>
              <tr>
                <th scope="col">Rate shock</th>
                <th scope="col">Repriced P&amp;L</th>
                <th scope="col">Legs</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((rate) => {
                const rowUnit = withCurrency(rate.unit, currency) ?? rate.unit;
                return (
                  <tr key={rate.scenario_id}>
                    <th scope="row">{sciUnit(rate.bp, rate.bp_unit)}</th>
                    <td className={rate.scenario_pnl < 0 ? "negative" : ""}>
                      {sciUnit(rate.scenario_pnl, rowUnit)}
                    </td>
                    <td>{sci(rate.n_legs)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Scroll>
      </Stack>
    </article>
  );
}
