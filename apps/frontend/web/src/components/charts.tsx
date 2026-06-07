// The Tab-1 chart panels, built on the Plotly wrapper (ADR 0030).
//
// Each panel is self-labelling (answers "what am I looking at?") and reads a typed BFF
// response. A candlestick falls back to a line trace when OHLC is absent; the 3D IV surface
// is a mesh3d over (delta, maturity, implied_vol); the smile is a 2D scatter of vol vs delta.

import type { Data } from "plotly.js";

import type { AnalyticsMaturity, PriceHistoryResponse } from "../api";
import { Plot } from "./Plot";

export function PriceChart({ data }: { data: PriceHistoryResponse }) {
  const label = `${data.underlying} — daily price (OHLC candlestick)`;
  if (data.n_bars === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No daily bars for {data.underlying} in this window.</p>
      </figure>
    );
  }
  const dates = data.bars.map((bar) => bar.trade_date);
  // A line chart is an acceptable fallback only when OHLC is absent; here the bars always
  // carry OHLC, so a candlestick is the primary trace.
  const trace: Data = {
    type: "candlestick",
    x: dates,
    open: data.bars.map((bar) => bar.open),
    high: data.bars.map((bar) => bar.high),
    low: data.bars.map((bar) => bar.low),
    close: data.bars.map((bar) => bar.close),
    name: data.underlying,
  };
  return <Plot label={label} data={[trace]} layout={{ xaxis: { title: { text: "date" } } }} />;
}

export function VolSurface({ maturities }: { maturities: AnalyticsMaturity[] }) {
  const label = "Implied-volatility surface (vol vs delta vs maturity)";
  // Flatten every maturity's band points into a (delta, maturity, vol) point cloud for mesh3d.
  const deltas: number[] = [];
  const years: number[] = [];
  const vols: number[] = [];
  for (const maturity of maturities) {
    maturity.smile.deltas.forEach((delta, i) => {
      deltas.push(delta);
      years.push(maturity.maturity_years);
      vols.push(maturity.smile.implied_vols[i]);
    });
  }
  if (vols.length === 0) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No surface to plot yet.</p>
      </figure>
    );
  }
  const trace: Data = {
    type: "mesh3d",
    x: deltas,
    y: years,
    z: vols,
    name: "IV surface",
  };
  return (
    <Plot
      label={label}
      data={[trace]}
      layout={{
        scene: {
          xaxis: { title: { text: "delta" } },
          yaxis: { title: { text: "maturity (y)" } },
          zaxis: { title: { text: "implied vol" } },
        },
      }}
    />
  );
}

export function SmileChart({ maturity }: { maturity: AnalyticsMaturity }) {
  const label = `Smile — ${maturity.label} (implied vol vs delta)`;
  const trace: Data = {
    type: "scatter",
    mode: "lines+markers",
    x: maturity.smile.deltas,
    y: maturity.smile.implied_vols,
    name: maturity.label,
  };
  return (
    <Plot
      label={label}
      data={[trace]}
      layout={{
        xaxis: { title: { text: "delta" } },
        yaxis: { title: { text: "implied vol" } },
      }}
    />
  );
}
