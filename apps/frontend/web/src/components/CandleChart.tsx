import {
  type CandlestickData,
  CandlestickSeries,
  createChart,
  type IChartApi,
} from "lightweight-charts";
import { useEffect, useRef } from "react";

import type { DailyBar } from "../api";
import { sci, UNITS } from "../lib/format";
import { baseLightweightOptions, CHART_COLORS } from "./chartTheme";

export interface CandleChartProps {
  bars: DailyBar[];

  label: string;
}

const UP = CHART_COLORS.positive;
const DOWN = CHART_COLORS.negative;

const price2 = (n: number): string => sci(n);
const volFmt = (n: number): string => Math.round(n).toLocaleString();

export function CandleChart({ bars, label }: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const legendRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (container === null) return;

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

    const sorted = [...bars].sort((a, b) =>
      a.trade_date < b.trade_date ? -1 : a.trade_date > b.trade_date ? 1 : 0,
    );
    const data: CandlestickData[] = sorted.map((bar) => ({
      time: bar.trade_date,
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    }));
    series.setData(data);
    chart.timeScale().fitContent();

    const volumeByDate = new Map(sorted.map((bar) => [bar.trade_date, bar.volume]));

    const renderLegend = (
      date: string,
      ohlc: CandlestickData,
      volume: number | undefined,
    ): void => {
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
