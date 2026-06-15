// The daily OHLC candlestick, on TradingView's lightweight-charts.
//
// Plotly stays the charting dependency for the 3D IV surface, the smile, and the risk
// heatmaps — lightweight-charts draws compact 2D financial charts only. Candlesticks use
// it because its native pan/zoom/crosshair is markedly smoother than a Plotly candlestick.
//
// A read-out box tracks the crosshair: it shows the hovered bar's date, open / high (max) /
// low (min) / close and volume, falling back to the latest bar when the cursor is off the chart.
//
// lightweight-charts draws to a <canvas> jsdom does not implement, so component tests stub this
// module (see src/test/candleMock.tsx), mirroring how the Plotly wrapper is stubbed.

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  createChart,
  type CandlestickData,
  type IChartApi,
} from "lightweight-charts";

import type { DailyBar } from "../api";
import { sci, UNITS } from "../lib/format";
import { CHART_COLORS, baseLightweightOptions } from "./chartTheme";

export interface CandleChartProps {
  bars: DailyBar[];
  // A required, human-readable label that answers "what am I looking at?" for the panel.
  label: string;
}

// Up/down candle colours read positive/negative off the shared dark-panel theme tokens.
const UP = CHART_COLORS.positive;
const DOWN = CHART_COLORS.negative;

// OHLC prices are analytics quantities: scientific notation (the shared "$" unit is shown once
// at the end of the OHLC block). Volume is a traded-share count (a cardinality), not an analytics
// quantity, so it keeps its plain grouped rendering.
const price2 = (n: number): string => sci(n);
const volFmt = (n: number): string => Math.round(n).toLocaleString();

export function CandleChart({ bars, label }: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const legendRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) return;

    // autoSize (inside baseLightweightOptions) wires lightweight-charts' own ResizeObserver, so
    // the chart tracks the panel width without a manual resize handler (matching the Plotly
    // wrapper's responsiveness).
    const chart: IChartApi = createChart(container, {
      ...baseLightweightOptions(),
      timeScale: { borderColor: CHART_COLORS.axis },
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
        `C ${price2(ohlc.close)} ${UNITS.price}   Vol ${vol}`;
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
