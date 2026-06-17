import type { Data, Layout } from "plotly.js";

import type { BacktestDay } from "../api";
import { UNITS } from "../lib/format";
import { Grid, Stack } from "./layout";
import { Plot } from "./Plot";

const GREEKS = [
  { key: "delta", label: "Delta", unit: UNITS.delta },
  { key: "gamma", label: "Gamma", unit: UNITS.gamma },
  { key: "vega", label: "Vega", unit: UNITS.vega },
  { key: "theta", label: "Theta", unit: UNITS.theta },
] as const;

export function GreeksOverTime({ days, kicker }: { days: BacktestDay[]; kicker: string }) {
  if (days.length === 0) {
    return (
      <article className="panel" aria-label="Exposure Greeks over time (empty)">
        <Stack gap="md">
          <div className="panel-heading">
            <div>
              <p className="panel-kicker">{kicker}</p>
              <h2>Exposure over time</h2>
            </div>
          </div>
          <p role="status">No days in this backtest window.</p>
        </Stack>
      </article>
    );
  }

  const dates = days.map((day) => day.as_of);

  return (
    <article className="panel" aria-label="Exposure Greeks over time">
      <Stack gap="md">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{kicker}</p>
            <h2>Exposure over time</h2>
          </div>
          <span className="status">{days.length} days</span>
        </div>
        <p>
          How the line&apos;s risk exposure moved each day. <strong>Delta</strong> is directional
          exposure, <strong>gamma</strong> how fast it changes, <strong>vega</strong> sensitivity to
          vol, <strong>theta</strong> the daily time decay. Each panel carries its own unit.
        </p>
        <Grid min="280px" gap="sm">
          {GREEKS.map((greek) => {
            const values = days.map((day) => day.greeks[greek.key]);
            const trace: Data = {
              type: "scatter",
              mode: "lines",
              x: dates,
              y: values,
              name: greek.label,
              line: { width: 2 },
            };
            const layout: Partial<Layout> = {
              xaxis: { title: { text: "trade date" } },
              yaxis: { title: { text: `${greek.label} (${greek.unit})` } },
            };
            return (
              <Plot
                key={greek.key}
                label={`${greek.label} over time, ${kicker} (${greek.unit})`}
                data={[trace]}
                layout={layout}
                height={260}
              />
            );
          })}
        </Grid>
      </Stack>
    </article>
  );
}
