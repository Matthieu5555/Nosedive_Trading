import { FormEvent, useState } from "react";

import type { ScenarioResult, SpotLadderPoint } from "../api";
import { postJson } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { BarChart } from "../components/BarChart";
import { LineChart } from "../components/LineChart";
import { Metric } from "../components/Metric";
import { useFetch } from "../hooks/useFetch";
import { money, number, signedMoney } from "../lib/format";

const LADDER_GREEKS = ["delta", "gamma", "vega", "theta"] as const;
type LadderGreek = (typeof LADDER_GREEKS)[number];

const GREEK_DIGITS: Record<LadderGreek, number> = {
  delta: 3,
  gamma: 5,
  vega: 2,
  theta: 2,
};

export function RiskScenariosPage() {
  const [spotShock, setSpotShock] = useState(-3);
  const [volShock, setVolShock] = useState(5);
  const [timeRoll, setTimeRoll] = useState(2);
  const [result, setResult] = useState<ScenarioResult | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const baseline = useFetch<ScenarioResult>("/api/risk/scenarios?underlying=SPX");
  const active = result ?? baseline.data;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    try {
      const next = await postJson<ScenarioResult>("/api/risk/scenarios", {
        underlying: "SPX",
        portfolio_id: "CORE-INDEX-OPTIONS",
        spot_shock_percent: spotShock,
        vol_shock_points: volShock,
        time_roll_days: timeRoll,
      });
      setResult(next);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "Unknown error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">Scenario engine</p>
          <h1>Risk scenarios</h1>
        </div>
        <form className="scenario-form" onSubmit={submit}>
          <label>
            Spot
            <input
              type="number"
              value={spotShock}
              min={-50}
              max={50}
              step={0.5}
              onChange={(event) => setSpotShock(Number(event.target.value))}
            />
          </label>
          <label>
            Vol
            <input
              type="number"
              value={volShock}
              min={-50}
              max={50}
              step={0.5}
              onChange={(event) => setVolShock(Number(event.target.value))}
            />
          </label>
          <label>
            Days
            <input
              type="number"
              value={timeRoll}
              min={0}
              max={365}
              step={1}
              onChange={(event) => setTimeRoll(Number(event.target.value))}
            />
          </label>
          <button type="submit" disabled={submitting}>
            Run
          </button>
        </form>
      </div>

      {submitError && <div className="state-panel state-panel-error">{submitError}</div>}

      <AsyncBlock loading={baseline.loading && !active} error={baseline.error}>
        {active && <ScenarioBoard result={active} />}
      </AsyncBlock>
    </section>
  );
}

function ScenarioBoard({ result }: { result: ScenarioResult }) {
  const maxAbsPnl = Math.max(...result.grid.map((point) => Math.abs(point.pnl)), 1);
  return (
    <div className="risk-grid">
      <article className="panel scenario-summary">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">Scenario {result.scenario_id}</p>
            <h2>{result.requested.portfolio_id}</h2>
          </div>
          <span className={result.pnl >= 0 ? "status positive" : "status negative"}>
            {signedMoney(result.pnl)}
          </span>
        </div>
        <div className="quote-strip">
          <Metric label="Baseline" value={money(result.baseline_value, "USD", 0)} />
          <Metric label="Shocked" value={money(result.shocked_value, "USD", 0)} />
          <Metric label="Spot" value={`${result.requested.spot_shock_percent}%`} />
          <Metric label="Vol" value={`${result.requested.vol_shock_points} pts`} />
          <Metric label="Time" value={`${result.requested.time_roll_days} d`} />
        </div>
      </article>

      <article className="panel greek-compare">
        <div className="panel-heading">
          <h2>Greek shift</h2>
          <span className="status">{result.provenance.provider}</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Greek</th>
                <th>Before</th>
                <th>After</th>
              </tr>
            </thead>
            <tbody>
              {(["delta", "gamma", "vega", "theta", "rho"] as const).map((key) => (
                <tr key={key}>
                  <td>{key.toUpperCase()}</td>
                  <td>{number(result.greek_before[key], key === "gamma" ? 5 : 2)}</td>
                  <td>{number(result.greek_after[key], key === "gamma" ? 5 : 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>

      <article className="panel ladder-panel">
        <div className="panel-heading">
          <h2>Spot ladder</h2>
          <span className="status">
            vol {result.requested.vol_shock_points} pts / {result.requested.time_roll_days} d
          </span>
        </div>
        <div className="chart-row">
          <div className="chart-block chart-block-wide">
            <p className="chart-title">PnL vs spot shock</p>
            <LineChart
              ariaLabel="Portfolio PnL by spot shock"
              series={[
                {
                  id: "pnl",
                  points: result.ladder.map((point) => ({
                    x: point.spot_shock_percent,
                    y: point.pnl,
                  })),
                },
              ]}
              markerX={result.requested.spot_shock_percent}
              formatX={(value) => `${value}%`}
            />
          </div>
          {LADDER_GREEKS.map((greek) => (
            <GreekLadderChart key={greek} greek={greek} ladder={result.ladder} />
          ))}
        </div>
      </article>

      <article className="panel expiry-panel">
        <div className="panel-heading">
          <h2>Greeks by expiry</h2>
          <span className="status">{result.expiry_buckets.length} expiries</span>
        </div>
        <div className="chart-row">
          <div className="chart-block">
            <p className="chart-title">Vega</p>
            <BarChart
              ariaLabel="Vega by expiry"
              bars={result.expiry_buckets.map((bucket) => ({
                label: bucket.expiry,
                value: bucket.greeks.vega,
              }))}
            />
          </div>
          <div className="chart-block">
            <p className="chart-title">Theta</p>
            <BarChart
              ariaLabel="Theta by expiry"
              bars={result.expiry_buckets.map((bucket) => ({
                label: bucket.expiry,
                value: bucket.greeks.theta,
              }))}
            />
          </div>
          <div className="chart-block">
            <p className="chart-title">Gamma</p>
            <BarChart
              ariaLabel="Gamma by expiry"
              bars={result.expiry_buckets.map((bucket) => ({
                label: bucket.expiry,
                value: bucket.greeks.gamma,
              }))}
              formatValue={(value) => number(value, 4)}
            />
          </div>
        </div>
      </article>

      <article className="panel heatmap-panel">
        <div className="panel-heading">
          <h2>Spot × vol PnL</h2>
          <span className="status">{result.grid.length}</span>
        </div>
        <div className="scenario-heatmap">
          {result.grid.map((point) => {
            const intensity = Math.abs(point.pnl) / maxAbsPnl;
            return (
              <div
                key={`${point.spot_shock_percent}-${point.vol_shock_points}`}
                className={point.pnl >= 0 ? "scenario-cell positive-cell" : "scenario-cell negative-cell"}
                style={{ "--heat": intensity.toString() } as React.CSSProperties}
              >
                <span>{signedMoney(point.pnl)}</span>
                <small>
                  {point.spot_shock_percent}% / {point.vol_shock_points} pts
                </small>
              </div>
            );
          })}
        </div>
      </article>
    </div>
  );
}

function GreekLadderChart({ greek, ladder }: { greek: LadderGreek; ladder: SpotLadderPoint[] }) {
  return (
    <div className="chart-block">
      <p className="chart-title">{greek.toUpperCase()}</p>
      <LineChart
        ariaLabel={`${greek} by spot shock`}
        series={[
          {
            id: greek,
            points: ladder.map((point) => ({ x: point.spot_shock_percent, y: point[greek] })),
          },
        ]}
        formatY={(value) => number(value, GREEK_DIGITS[greek])}
        formatX={(value) => `${value}%`}
      />
    </div>
  );
}
