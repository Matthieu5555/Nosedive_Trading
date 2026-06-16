import {
  createYieldCurveChart,
  type IYieldCurveChartApi,
  type LineData,
  LineSeries,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import { sci } from "../lib/format";
import { baseLightweightOptions } from "./chartTheme";

export interface LightweightLinePoint {
  x: number;
  label: string;
  value: number;
}

export interface LightweightLineSeries {
  label: string;
  color: string;
  points: LightweightLinePoint[];
}

export interface LightweightLineChartProps {
  label: string;
  series: LightweightLineSeries[];
  yUnit: string;
  xFormatter?: (x: number) => string;
  valueFormatter?: (value: number) => string;
}

function defaultXFormatter(x: number): string {
  if (x < 12) return `${x}m`;
  if (x % 12 === 0) return `${x / 12}y`;
  return `${Math.floor(x / 12)}y ${x % 12}m`;
}

// The series values are analytics quantities (dollar Greeks, etc.): scientific notation by
// default. The unit rides the panel's yUnit (shown in the legend / axis title), so the bare
// number is correct here; a caller with a different rule passes its own valueFormatter.
function defaultValueFormatter(value: number): string {
  return sci(value);
}

function sortedUniqueData(points: LightweightLinePoint[]): LineData<number>[] {
  const byX = new Map<number, number>();
  for (const point of points) {
    if (Number.isFinite(point.x) && Number.isFinite(point.value)) {
      byX.set(point.x, point.value);
    }
  }
  return [...byX.entries()].sort((a, b) => a[0] - b[0]).map(([time, value]) => ({ time, value }));
}

export function LightweightLineChart({
  label,
  series,
  yUnit,
  xFormatter = defaultXFormatter,
  valueFormatter = defaultValueFormatter,
}: LightweightLineChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const legendRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    const legend = legendRef.current;
    if (container === null || legend === null) return;

    const maxX = Math.max(12, ...series.flatMap((item) => item.points.map((point) => point.x)));
    const base = baseLightweightOptions();
    const chart: IYieldCurveChartApi = createYieldCurveChart(container, {
      ...base,
      // The in-canvas TradingView logo sat bottom-left over the axis labels. Drop it so nothing is
      // painted on top of the data (lightweight-charts is Apache-2.0; the logo is optional).
      layout: { ...base.layout, attributionLogo: false },
      localization: {
        priceFormatter: valueFormatter,
      },
      yieldCurve: {
        baseResolution: 1,
        minimumTimeRange: maxX,
        startTimeRange: 0,
        formatTime: xFormatter,
      },
    });

    const rendered = series
      .map((item) => {
        const data = sortedUniqueData(item.points);
        if (data.length === 0) return null;
        const api = chart.addSeries(LineSeries, {
          color: item.color,
          lineWidth: 2,
          priceLineVisible: false,
          lastValueVisible: false,
        });
        api.setData(data);
        return {
          api,
          label: item.label,
          color: item.color,
          latest: data[data.length - 1],
          valueByX: new Map(data.map((point) => [point.time, point.value])),
        };
      })
      .filter((item) => item !== null);

    chart.timeScale().fitContent();

    // Build a compact, wrapping legend ABOVE the plot: a muted "x · unit" token, then one
    // swatch + label + value chip per series. Rebuilding real nodes (not one nowrap text line over
    // the canvas) keeps the legend off the data so it can never collide with the curves, and lets
    // it wrap onto multiple rows instead of overflowing the panel the way the old run-on line did.
    legend.replaceChildren();
    const xToken = document.createElement("span");
    xToken.className = "legend-x";
    legend.appendChild(xToken);
    const valueNodes = rendered.map((item) => {
      const chip = document.createElement("span");
      chip.className = "legend-item";
      const swatch = document.createElement("i");
      swatch.className = "legend-swatch";
      swatch.style.backgroundColor = item.color;
      const name = document.createElement("span");
      name.className = "legend-label";
      name.textContent = item.label;
      const value = document.createElement("span");
      value.className = "legend-value";
      chip.append(swatch, name, value);
      legend.appendChild(chip);
      return value;
    });

    const renderLegend = (x: number | null): void => {
      const xValue = x === null ? (rendered[0]?.latest.time ?? 0) : x;
      xToken.textContent = `${xFormatter(xValue)} · ${yUnit}`;
      rendered.forEach((item, index) => {
        const value = x === null ? item.latest.value : item.valueByX.get(x);
        valueNodes[index].textContent = value === undefined ? "—" : valueFormatter(value);
      });
    };

    renderLegend(null);

    const onMove: Parameters<IYieldCurveChartApi["subscribeCrosshairMove"]>[0] = (param) => {
      if (typeof param.time !== "number") {
        renderLegend(null);
        return;
      }
      renderLegend(param.time);
    };
    chart.subscribeCrosshairMove(onMove);

    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
    };
  }, [series, xFormatter, valueFormatter, yUnit]);

  if (!series.some((item) => item.points.length > 0)) {
    return (
      <figure aria-label={label} className="plot">
        <figcaption>{label}</figcaption>
        <p>No line data for this view.</p>
      </figure>
    );
  }

  return (
    <figure aria-label={label} className="plot lightweight-line-figure">
      <figcaption>{label}</figcaption>
      <div ref={legendRef} className="lightweight-line-legend" aria-hidden="true" />
      <div ref={containerRef} className="lightweight-line-chart" />
    </figure>
  );
}
