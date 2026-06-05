import { useMemo, useState } from "react";

import type {
  MarketDashboard,
  OptionQuote,
  UnderlyingsResponse,
  VolSurfacePoint,
  VolSurfaceSlice,
} from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { Metric } from "../components/Metric";
import { useFetch } from "../hooks/useFetch";
import { money, number, percent, volPercent } from "../lib/format";

export function MarketPage() {
  const [underlying, setUnderlying] = useState("SPX");
  const [expiry, setExpiry] = useState("all");
  const underlyings = useFetch<UnderlyingsResponse>("/api/underlyings");
  const market = useFetch<MarketDashboard>(`/api/market?underlying=${underlying}`, 15_000);

  const expiries = useMemo(() => {
    const options = market.data?.option_chain ?? [];
    return ["all", ...Array.from(new Set(options.map((quote) => quote.expiry)))];
  }, [market.data]);

  const options = useMemo(() => {
    const chain = market.data?.option_chain ?? [];
    return expiry === "all" ? chain : chain.filter((quote) => quote.expiry === expiry);
  }, [expiry, market.data]);

  return (
    <section className="page">
      <div className="page-header">
        <div>
          <p className="eyebrow">S&P operator board</p>
          <h1>Market snapshots</h1>
        </div>
        <div className="control-row">
          <select
            aria-label="Underlying"
            value={underlying}
            onChange={(event) => {
              setUnderlying(event.target.value);
              setExpiry("all");
            }}
          >
            {(underlyings.data?.underlyings ?? [{ symbol: "SPX", name: "S&P 500 Index" }]).map(
              (item) => (
                <option key={item.symbol} value={item.symbol}>
                  {item.symbol} — {item.name}
                </option>
              ),
            )}
          </select>
          <select aria-label="Expiry" value={expiry} onChange={(event) => setExpiry(event.target.value)}>
            {expiries.map((item) => (
              <option key={item} value={item}>
                {item === "all" ? "All expiries" : item}
              </option>
            ))}
          </select>
        </div>
      </div>

      <AsyncBlock loading={market.loading} error={market.error}>
        {market.data && <MarketBoard dashboard={market.data} options={options} />}
      </AsyncBlock>
    </section>
  );
}

function MarketBoard({ dashboard, options }: { dashboard: MarketDashboard; options: OptionQuote[] }) {
  const snapshot = dashboard.index_snapshot;
  return (
    <div className="market-grid">
      <article className="panel index-panel">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{dashboard.underlying.name}</p>
            <h2>{snapshot.symbol}</h2>
          </div>
          <span className={snapshot.change_percent >= 0 ? "status positive" : "status negative"}>
            {percent(snapshot.change_percent)}
          </span>
        </div>
        <div className="quote-strip">
          <Metric label="Last" value={money(snapshot.last, snapshot.currency)} />
          <Metric label="Bid" value={money(snapshot.bid, snapshot.currency)} />
          <Metric label="Ask" value={money(snapshot.ask, snapshot.currency)} />
          <Metric label="Volume" value={number(snapshot.volume, 0)} />
        </div>
      </article>

      <article className="panel greeks-panel">
        <div className="panel-heading">
          <h2>Greeks</h2>
          <span className="status">Aggregate</span>
        </div>
        <div className="greek-grid">
          <Metric label="Delta" value={number(dashboard.greek_totals.delta, 3)} />
          <Metric label="Gamma" value={number(dashboard.greek_totals.gamma, 5)} />
          <Metric label="Vega" value={number(dashboard.greek_totals.vega, 2)} />
          <Metric label="Theta" value={number(dashboard.greek_totals.theta, 2)} />
          <Metric label="Rho" value={number(dashboard.greek_totals.rho, 2)} />
        </div>
      </article>

      <article className="panel stocks-panel">
        <div className="panel-heading">
          <h2>Stocks</h2>
          <span className="status">{dashboard.stock_snapshots.length}</span>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Last</th>
                <th>Bid</th>
                <th>Ask</th>
                <th>Move</th>
              </tr>
            </thead>
            <tbody>
              {dashboard.stock_snapshots.map((row) => (
                <tr key={row.symbol}>
                  <td>
                    <strong>{row.symbol}</strong>
                    <span>{row.name}</span>
                  </td>
                  <td>{money(row.last, row.currency)}</td>
                  <td>{money(row.bid, row.currency)}</td>
                  <td>{money(row.ask, row.currency)}</td>
                  <td className={row.change_percent >= 0 ? "positive" : "negative"}>
                    {percent(row.change_percent)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>

      <article className="panel surface-panel">
        <div className="panel-heading">
          <div>
            <p className="panel-kicker">{dashboard.volatility_surface.underlying}</p>
            <h2>Volatility surface</h2>
          </div>
          <span className="status">{dashboard.provenance.provider}</span>
        </div>
        <VolSurface
          points={dashboard.volatility_surface.points}
          slices={dashboard.volatility_surface.slices}
        />
        <div className="surface-slices">
          {dashboard.volatility_surface.slices.map((slice) => (
            <div key={slice.expiry}>
              <span>ATM {slice.expiry}</span>
              <strong>{volPercent(slice.atm_vol)}</strong>
              <small>RMSE {slice.rmse.toFixed(4)}</small>
            </div>
          ))}
        </div>
      </article>

      <article className="panel options-panel">
        <div className="panel-heading">
          <h2>Options bid / ask</h2>
          <span className="status">{options.length}</span>
        </div>
        <div className="table-wrap option-table">
          <table>
            <thead>
              <tr>
                <th>Expiry</th>
                <th>Type</th>
                <th>Strike</th>
                <th>Bid</th>
                <th>Ask</th>
                <th>IV</th>
                <th>Delta</th>
                <th>Gamma</th>
                <th>Vega</th>
                <th>Theta</th>
              </tr>
            </thead>
            <tbody>
              {options.map((quote) => (
                <tr key={quote.contract_key}>
                  <td>{quote.expiry}</td>
                  <td>{quote.option_type.toUpperCase()}</td>
                  <td>{number(quote.strike, 0)}</td>
                  <td>{number(quote.bid)}</td>
                  <td>{number(quote.ask)}</td>
                  <td>{(quote.implied_vol * 100).toFixed(1)}%</td>
                  <td>{number(quote.greeks.delta, 3)}</td>
                  <td>{number(quote.greeks.gamma, 5)}</td>
                  <td>{number(quote.greeks.vega, 2)}</td>
                  <td>{number(quote.greeks.theta, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>
    </div>
  );
}

function VolSurface({ points, slices }: { points: VolSurfacePoint[]; slices: VolSurfaceSlice[] }) {
  const maxVol = Math.max(...points.map((point) => point.implied_vol));
  const minVol = Math.min(...points.map((point) => point.implied_vol));
  const maturities = Array.from(new Set(points.map((point) => point.maturity_years))).sort((a, b) => a - b);
  const moneyness = Array.from(new Set(points.map((point) => point.log_moneyness))).sort((a, b) => a - b);
  const expiryByMaturity = new Map(slices.map((slice) => [slice.maturity_years, slice.expiry]));

  return (
    <table className="surface-table" aria-label="Implied volatility by expiry and log-moneyness">
      <thead>
        <tr>
          <th scope="col">Expiry \ log-m</th>
          {moneyness.map((logMoneyness) => (
            <th key={logMoneyness} scope="col">
              {logMoneyness.toFixed(2)}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {maturities.map((maturity) => (
          <tr key={maturity}>
            <th scope="row">{expiryByMaturity.get(maturity) ?? `${maturity.toFixed(2)}y`}</th>
            {moneyness.map((logMoneyness) => {
              const point = points.find(
                (item) => item.maturity_years === maturity && item.log_moneyness === logMoneyness,
              );
              if (!point) {
                return <td key={logMoneyness}>—</td>;
              }
              const intensity = (point.implied_vol - minVol) / Math.max(maxVol - minVol, 0.0001);
              return (
                <td
                  key={logMoneyness}
                  className="surface-cell"
                  style={{ "--heat": intensity.toString() } as React.CSSProperties}
                >
                  {volPercent(point.implied_vol)}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
