import type { BasketLegResult, BasketMetric, BasketRiskResponse } from "../api";
import { sci, sciUnit, withCurrency } from "../lib/format";
import { Plot } from "./Plot";

const GREEK_ORDER = ["delta", "gamma", "vega", "theta", "rho"] as const;
type GreekName = (typeof GREEK_ORDER)[number];

function metricOf(leg: BasketLegResult, greek: GreekName): BasketMetric {
  return leg.metrics[greek];
}

function legLabel(leg: BasketLegResult): string {
  if (leg.instrument_kind === "stock")
    return `${leg.side} ${leg.quantity} ${leg.underlying} (stock)`;
  return `${leg.side} ${leg.quantity} ${leg.underlying} ${leg.tenor_label}/${leg.delta_band}`;
}

export function BasketRiskPanel({
  result,
  currency = "$",
}: {
  result: BasketRiskResponse;
  // The currency symbol the monetized dollar Greeks/price should render in (the index quote
  // currency). Defaults to "$" so a USD/unknown-currency view is unchanged.
  currency?: string;
}) {
  const totalsLabel = `Basket dollar Greeks — ${result.basket_id} (book-additive sum)`;
  const perLegLabel = "Per-leg contribution to each dollar Greek";

  // The per-leg dollar-delta contribution bar (the one place a chart beats a table).
  const deltaLabel = "Per-leg Delta$ contribution";
  const deltaBar = {
    type: "bar" as const,
    x: result.legs.map(legLabel),
    y: result.legs.map((leg) => metricOf(leg, "delta").dollar ?? 0),
  };

  return (
    <section aria-label={totalsLabel}>
      <table aria-label={totalsLabel}>
        <caption>{totalsLabel}</caption>
        <thead>
          <tr>
            <th>Greek</th>
            <th>$ value</th>
            <th>unit</th>
          </tr>
        </thead>
        <tbody>
          {GREEK_ORDER.map((greek) => {
            const metric = result.metrics[greek];
            return (
              <tr key={greek}>
                <td>{greek}</td>
                <td>{sci(metric.dollar)}</td>
                <td>{withCurrency(metric.unit, currency) ?? "n/a"}</td>
              </tr>
            );
          })}
          <tr>
            <td>price</td>
            <td>{sci(result.price)}</td>
            <td>{withCurrency("$ (net leg value)", currency)}</td>
          </tr>
        </tbody>
      </table>

      <table aria-label={perLegLabel}>
        <caption>{perLegLabel}</caption>
        <thead>
          <tr>
            <th>Leg</th>
            <th>resolved</th>
            {GREEK_ORDER.map((greek) => (
              <th key={greek}>{greek} $</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.legs.map((leg, index) => (
            <tr key={index} aria-label={legLabel(leg)}>
              <td>{legLabel(leg)}</td>
              <td>{leg.resolved ? "yes" : leg.gap_reason}</td>
              {GREEK_ORDER.map((greek) => {
                const metric = metricOf(leg, greek);
                return (
                  <td key={greek}>{sciUnit(metric.dollar, withCurrency(metric.unit, currency))}</td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>

      <Plot data={[deltaBar]} label={deltaLabel} layout={{ height: 260 }} />

      {result.gaps.length > 0 && (
        <div role="alert" className="gaps" aria-label="basket gaps">
          <h4>Gaps ({result.n_gaps})</h4>
          <ul>
            {result.gaps.map((gap, index) => (
              <li key={index}>
                {gap.underlying}
                {gap.tenor_label ? ` ${gap.tenor_label}/${gap.delta_band}` : ""}: {gap.reason}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
