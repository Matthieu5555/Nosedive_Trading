// Reusable TradingView Lightweight Charts line panel for dense 2D analytical curves.
//
// The component is intentionally narrow: it draws numeric-x line series with a crosshair
// read-out and a formatter supplied by the caller. Plotly remains the charting path for
// 3D surfaces, heatmaps, and non-line analytical views.

import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  LineSeries,
  createYieldCurveChart,
  type IYieldCurveChartApi,
  type LineData,
} from "lightweight-charts";

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

const GRID = "#2b302c";
const AXIS = "#454d45";
const MUTED = "#8f978f";

function defaultXFormatter(x: number): string {
  if (x < 12) return `${x}m`;
  if (x % 12 === 0) return `${x / 12}y`;
  return `${Math.floor(x / 12)}y ${x % 12}m`;
}

function defaultValueFormatter(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 6 });
}

function sortedUniqueData(points: LightweightLinePoint[]): LineData<number>[] {
  const byX = new Map<number, number>();
  for (const point of points) {
    if (Number.isFinite(point.x) && Number.isFinite(point.value)) {
      byX.set(point.x, point.value);
    }
  }
  return [...byX.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([time, value]) => ({ time, value }));
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
    if (container === null) return;

    const maxX = Math.max(12, ...series.flatMap((item) => item.points.map((point) => point.x)));
    const chart: IYieldCurveChartApi = createYieldCurveChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "rgba(0,0,0,0)" },
        textColor: MUTED,
        fontFamily: '"Basis Grotesque", Inter, sans-serif',
        fontSize: 11,
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: GRID },
        horzLines: { color: GRID },
      },
      rightPriceScale: { borderColor: AXIS },
      crosshair: { mode: CrosshairMode.Normal },
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
          latest: data[data.length - 1],
          valueByX: new Map(data.map((point) => [point.time, point.value])),
        };
      })
      .filter((item) => item !== null);

    chart.timeScale().fitContent();

    const renderLegend = (x: number | null): void => {
      const legend = legendRef.current;
      if (legend === null) return;
      const values = rendered
        .map((item) => {
          const value = x === null ? item.latest.value : item.valueByX.get(x);
          return value === undefined ? null : `${item.label} ${valueFormatter(value)}`;
        })
        .filter((item) => item !== null);
      const xLabel = x === null ? xFormatter(rendered[0]?.latest.time ?? 0) : xFormatter(x);
      legend.textContent = `${xLabel}   ${values.join("   ")}   ${yUnit}`;
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
      <div ref={containerRef} className="lightweight-line-chart">
        <div ref={legendRef} className="lightweight-line-legend" aria-hidden="true" />
      </div>
    </figure>
  );
}
