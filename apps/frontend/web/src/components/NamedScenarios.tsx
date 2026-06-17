import { Scroll, Stack } from "../components/layout";
import { percent, sci, sciUnit, withCurrency } from "../lib/format";
import type { NamedScenario } from "../stressApi";

const SHOCK_UNIT = "(frac)";

export function NamedScenarios({
  scenarios,
  currency,
  emptyMessage = "No named historical scenarios are configured for this selection.",
}: {
  scenarios: NamedScenario[];
  currency?: string;
  emptyMessage?: string;
}) {
  if (scenarios.length === 0) {
    return (
      <article className="panel" aria-label="Named scenarios (empty)">
        <p role="status">{emptyMessage}</p>
      </article>
    );
  }

  const ordered = [...scenarios].sort((a, b) => a.scenario_pnl - b.scenario_pnl);
  const worst = ordered[0];
  const worstUnit = withCurrency(worst.unit, currency) ?? worst.unit;

  return (
    <article className="panel named-scenarios" aria-label="Named historical scenarios">
      <Stack gap="md">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">Worst case: {worst.label}</p>
            <h2>Crisis replays, worst loss first</h2>
          </div>
          <span className={worst.scenario_pnl < 0 ? "status negative" : "status"}>
            {sciUnit(worst.scenario_pnl, worstUnit)}
          </span>
        </div>
        <p>
          Each row replays a labelled historical stress (a one-shot move in spot, vol and rate at
          once) against today&apos;s book and shows the full-reprice P&amp;L. Most negative first,
          that is the worst case. P&amp;L unit: <strong>{worstUnit}</strong>; shocks are fractional
          moves.
        </p>
        <Scroll>
          <table aria-label="Named historical scenarios">
            <thead>
              <tr>
                <th scope="col">Scenario</th>
                <th scope="col">Spot shock</th>
                <th scope="col">Vol shock</th>
                <th scope="col">Rate shock</th>
                <th scope="col">Stressed P&amp;L</th>
                <th scope="col">Legs</th>
              </tr>
            </thead>
            <tbody>
              {ordered.map((scenario) => {
                const unit = withCurrency(scenario.unit, currency) ?? scenario.unit;
                return (
                  <tr key={scenario.scenario_id}>
                    <th scope="row">{scenario.label}</th>
                    <td>{percent(scenario.spot_shock * 100)}</td>
                    <td>{sciUnit(scenario.vol_shock, SHOCK_UNIT)}</td>
                    <td>
                      {scenario.rate_shock === null
                        ? "-"
                        : sciUnit(scenario.rate_shock, SHOCK_UNIT)}
                    </td>
                    <td className={scenario.scenario_pnl < 0 ? "negative" : ""}>
                      {sciUnit(scenario.scenario_pnl, unit)}
                    </td>
                    <td>{sci(scenario.n_legs)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Scroll>
      </Stack>
    </article>
  );
}
