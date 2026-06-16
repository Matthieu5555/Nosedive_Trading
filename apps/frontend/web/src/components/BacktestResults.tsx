import type { BacktestResult } from "../api";
import { sci, sciUnit, withCurrency } from "../lib/format";
import { EquityCurve } from "./EquityCurve";
import { GreeksOverTime } from "./GreeksOverTime";
import { Metric } from "./Metric";
import { WhichGreekPaid } from "./WhichGreekPaid";

export function BacktestResults({
  result,
  currency,
}: {
  result: BacktestResult;
  currency: string;
}) {
  const { summary, days } = result;
  const moneyUnit = withCurrency("$", currency) ?? "$";
  const kicker = `${result.strategy_id} (${days.length} days)`;

  return (
    <div className="backtest-results">
      <article className="panel" aria-label="Backtest summary">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{kicker}</p>
            <h2>How the line did</h2>
          </div>
          <span className={summary.total_net_pnl < 0 ? "status negative" : "status"}>
            net {sciUnit(summary.total_net_pnl, moneyUnit)}
          </span>
        </div>
        <p>
          The headline scorecard. <strong>Net P&amp;L</strong> is what the book kept after costs;{" "}
          <strong>Sharpe</strong> is return per unit of risk (higher is steadier);{" "}
          <strong>max drawdown</strong> is the worst peak-to-trough dip; <strong>worst stress</strong>{" "}
          is the deepest loss across the what-if shock grid.
        </p>
        <div className="quote-strip">
          <Metric label="Net P&L" value={sciUnit(summary.total_net_pnl, moneyUnit)} />
          <Metric label="Gross P&L" value={sciUnit(summary.total_pnl, moneyUnit)} />
          <Metric label="Transaction cost" value={sciUnit(summary.total_transaction_cost, moneyUnit)} />
          <Metric label="Max drawdown" value={sciUnit(summary.max_drawdown, moneyUnit)} />
          <Metric label="Sharpe" value={sci(summary.sharpe)} />
          <Metric label="Turnover" value={sci(summary.turnover)} />
          <Metric label="Worst stress loss" value={sciUnit(summary.worst_stress_loss, moneyUnit)} />
        </div>
      </article>

      <EquityCurve days={days} currency={currency} kicker={result.strategy_id} />

      <WhichGreekPaid
        attribution={result.cumulative_attribution}
        currency={currency}
        kicker={result.strategy_id}
      />

      <GreeksOverTime days={days} kicker={result.strategy_id} />
    </div>
  );
}
