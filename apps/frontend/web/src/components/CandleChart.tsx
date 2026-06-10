// The daily OHLC candlestick, on TradingView's lightweight-charts (this panel only).
//
// Plotly stays the charting dependency for the 3D IV surface, the smile, and the risk
// heatmaps — lightweight-charts draws 2D time series only and cannot render those. The
// candlestick alone moves here because lightweight-charts' native pan/zoom/crosshair is
// markedly smoother than a Plotly candlestick; the rest of the app is unchanged.
//
// A read-out box tracks the crosshair: it shows the hovered bar's date, open / high (max) /
// low (min) / close and volume, falling back to the latest bar when the cursor is off the chart.
//
// lightweight-charts draws to a <canvas> jsdom does not implement, so component tests stub this
// module (see src/test/candleMock.tsx), mirroring how the Plotly wrapper is stubbed.

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  type CandlestickData,
  type IChartApi,
} from "lightweight-charts";

import type { DailyBar } from "../api";

export interface CandleChartProps {
  bars: DailyBar[];
  // A required, human-readable label that answers "what am I looking at?" for the panel.
  label: string;
}

// Theme tokens mirrored from src/index.css so the chart sits inside the dark panel grammar.
const GRID = "#2b302c"; // --border
const AXIS = "#454d45"; // --border-strong
const MUTED = "#8f978f"; // --muted
const UP = "#a8e6ba"; // --positive
const DOWN = "#ef9c92"; // --negative

// Explicit, locale-aware rounding for the hover read-out — every displayed number passes through
// an explicit rounding (the design brief forbids raw floats on screen).
const price2 = (n: number): string =>
  n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const volFmt = (n: number): string => Math.round(n).toLocaleString();

export function CandleChart({ bars, label }: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const legendRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) return;

    // autoSize wires lightweight-charts' own ResizeObserver, so the chart tracks the panel
    // width without a manual resize handler (matching the Plotly wrapper's responsiveness).
    const chart: IChartApi = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "rgba(0,0,0,0)" },
        textColor: MUTED,
        fontFamily: '"Basis Grotesque", Inter, sans-serif',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: GRID },
        horzLines: { color: GRID },
      },
      rightPriceScale: { borderColor: AXIS },
      timeScale: { borderColor: AXIS },
      crosshair: { mode: CrosshairMode.Normal },
    });

    const series = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderUpColor: UP,
      borderDownColor: DOWN,
      wickUpColor: UP,
      wickDownColor: DOWN,
    });

    // lightweight-charts requires strictly ascending time, so sort by trade_date (a plain ISO
    // string) defensively before mapping — an out-of-order or duplicate point throws on setData.
    const sorted = [...bars].sort((a, b) =>
      a.trade_date < b.trade_date ? -1 : a.trade_date > b.trade_date ? 1 : 0,
    );
    const data: CandlestickData[] = sorted.map((bar) => ({
      time: bar.trade_date, // ISO yyyy-mm-dd is a valid business-day time
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    }));
    series.setData(data);
    chart.timeScale().fitContent();

    // Volume is not part of the candlestick series, so look it up by date for the read-out.
    const volumeByDate = new Map(sorted.map((bar) => [bar.trade_date, bar.volume]));

    const renderLegend = (date: string, ohlc: CandlestickData, volume: number | undefined): void => {
      const legend = legendRef.current;
      if (legend === null) return;
      const vol = volume === undefined ? "n/a" : volFmt(volume);
      legend.textContent =
        `${date}   O ${price2(ohlc.open)}   ` +
        `H ${price2(ohlc.high)}   L ${price2(ohlc.low)}   ` +
        `C ${price2(ohlc.close)}   Vol ${vol}`;
    };

    // Seed the read-out with the latest bar so the box is never empty before the first hover.
    const last = sorted[sorted.length - 1];
    if (last !== undefined) {
      renderLegend(last.trade_date, data[data.length - 1], last.volume);
    }

    const onMove: Parameters<IChartApi["subscribeCrosshairMove"]>[0] = (param) => {
      const point = param.seriesData.get(series) as CandlestickData | undefined;
      if (param.time === undefined || point === undefined) {
        // Cursor off the chart — fall back to the latest bar rather than blanking the box.
        if (last !== undefined) {
          renderLegend(last.trade_date, data[data.length - 1], last.volume);
        }
        return;
      }
      const date = String(param.time);
      renderLegend(date, point, volumeByDate.get(date));
    };
    chart.subscribeCrosshairMove(onMove);

    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      chart.remove();
    };
  }, [bars]);

  return (
    <figure aria-label={label} className="plot">
      <figcaption>{label}</figcaption>
      <div ref={containerRef} className="candle-chart">
        <div ref={legendRef} className="candle-legend" aria-hidden="true" />
      </div>
    </figure>
  );
}
