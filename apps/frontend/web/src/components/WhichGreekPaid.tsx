import type { Data, Layout } from "plotly.js";

import type { BacktestAttribution } from "../api";
import { sci, sciUnit, withCurrency } from "../lib/format";
import { Plot } from "./Plot";

const GREEKS = [
  { key: "delta", label: "Delta" },
  { key: "gamma", label: "Gamma" },
  { key: "vega", label: "Vega" },
  { key: "theta", label: "Theta" },
  { key: "rho", label: "Rho" },
  { key: "vanna", label: "Vanna" },
  { key: "volga", label: "Volga" },
] as const;

export function WhichGreekPaid({
  attribution,
  currency,
  kicker,
}: {
  attribution: BacktestAttribution;
  currency: string;
  kicker: string;
}) {
  const unit = withCurrency("$", currency) ?? "$";

  const terms = GREEKS.map((greek) => ({
    label: greek.label,
    dollars: attribution[greek.key],
  }));

  const total = terms.reduce((sum, term) => sum + term.dollars, 0);
  const leader = terms.reduce((best, term) =>
    Math.abs(term.dollars) > Math.abs(best.dollars) ? term : best,
  );

  const bars: Data = {
    type: "bar",
    x: terms.map((term) => term.label),
    y: terms.map((term) => term.dollars),
    text: terms.map((term) => `${sci(term.dollars)} (${unit})`),
    textposition: "outside",
    marker: {
      color: terms.map((term) => (term.dollars < 0 ? "#c0392b" : "#1e7e4f")),
    },
    name: "by-Greek P&L",
  } as unknown as Data;

  const layout: Partial<Layout> = {
    xaxis: { title: { text: "Greek" } },
    yaxis: { title: { text: `cumulative P&L (${unit})` } },
  };

  return (
    <article className="panel attribution-panel" aria-label="Which Greek paid">
      <div className="panel-heading">
        <div>
          <p className="panel-kicker">{kicker}</p>
          <h2>Where the return came from</h2>
        </div>
        <span className={leader.dollars < 0 ? "status negative" : "status"}>
          {leader.label} {leader.dollars < 0 ? "lost most" : "paid most"}
        </span>
      </div>
      <p>
        Each bar is one Greek&apos;s share of the whole backtest&apos;s P&amp;L, the plain-English
        answer to <em>where did the return come from</em>. Green paid, red cost. The largest bar is
        what drove the result; for a short-put line that is normally <strong>theta</strong> (carry
        earned) against <strong>gamma</strong>/<strong>vega</strong> (the tail paid for it). P&amp;L
        unit: <strong>{unit}</strong>.
      </p>
      <ul className="attribution-legend" aria-label="by-Greek contributions">
        {terms.map((term) => (
          <li key={term.label}>
            {term.label}: <strong>{sciUnit(term.dollars, unit)}</strong>
          </li>
        ))}
        <li>
          Total: <strong>{sciUnit(total, unit)}</strong>
        </li>
      </ul>
      <Plot
        label={`Which Greek paid, ${kicker} (cumulative by-Greek P&L contributions)`}
        data={[bars]}
        layout={layout}
        height={360}
      />
    </article>
  );
}
