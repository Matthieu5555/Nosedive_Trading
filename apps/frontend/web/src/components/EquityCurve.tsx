import type { Data, Layout } from "plotly.js";

import type { BacktestDay } from "../api";
import { sciUnit, withCurrency } from "../lib/format";
import { Metric } from "./Metric";
import { Plot } from "./Plot";

export function EquityCurve({
  days,
  currency,
  kicker,
}: {
  days: BacktestDay[];
  currency: string;
  kicker: string;
}) {
  const unit = withCurrency("$", currency) ?? "$";

  if (days.length === 0) {
    return (
      <article className="panel" aria-label="Cumulative P&L (empty)">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{kicker}</p>
            <h2>Cumulative P&amp;L</h2>
          </div>
        </div>
        <p role="status">No days in this backtest window.</p>
      </article>
    );
  }

  const dates = days.map((day) => day.as_of);
  const gross = days.map((day) => day.cumulative_pnl);
  const net = days.map((day) => day.cumulative_net_pnl);

  const grossTrace: Data = {
    type: "scatter",
    mode: "lines",
    x: dates,
    y: gross,
    name: "Gross (before costs)",
    line: { width: 2 },
  };
  const netTrace: Data = {
    type: "scatter",
    mode: "lines",
    x: dates,
    y: net,
    name: "Net (after costs)",
    line: { width: 2 },
  };

  const layout: Partial<Layout> = {
    xaxis: { title: { text: "trade date" } },
    yaxis: { title: { text: `cumulative P&L (${unit})` } },
    legend: { orientation: "h" },
  };

  const lastGross = gross[gross.length - 1];
  const lastNet = net[net.length - 1];

  return (
    <article className="panel" aria-label="Cumulative P&L">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">{kicker}</p>
          <h2>Cumulative P&amp;L</h2>
        </div>
        <span className={lastNet < 0 ? "status negative" : "status"}>
          {days.length} days
        </span>
      </div>
      <p>
        The running total of the line&apos;s P&amp;L, day by day. <strong>Gross</strong> is before
        trading costs; <strong>net</strong> is what the book actually keeps after commission and
        slippage. The gap between the two lines is the cost drag. P&amp;L unit: <strong>{unit}</strong>
        .
      </p>
      <div className="quote-strip">
        <Metric label="Ending gross" value={sciUnit(lastGross, unit)} />
        <Metric label="Ending net" value={sciUnit(lastNet, unit)} />
      </div>
      <Plot
        label={`Cumulative P&L — ${kicker} (gross vs net, by trade date)`}
        data={[grossTrace, netTrace]}
        layout={layout}
        height={360}
      />
    </article>
  );
}
