import type { RiskResponse } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { useFetch } from "../hooks/useFetch";

export function RiskPage() {
  const state = useFetch<RiskResponse>("/api/risk");

  return (
    <section>
      <h1>Portfolio Risk</h1>
      <AsyncBlock state={state}>
        {(data) =>
          data.n_aggregates === 0 ? (
            <p>No risk aggregates persisted yet.</p>
          ) : (
            <table>
              <caption>{data.n_aggregates} aggregate groups</caption>
              <thead>
                <tr>
                  <th>Portfolio</th>
                  <th>Group</th>
                  <th>Δ</th>
                  <th>Γ</th>
                  <th>V</th>
                  <th>Θ</th>
                </tr>
              </thead>
              <tbody>
                {data.aggregates.map((agg) => (
                  <tr key={`${agg.portfolio_id}:${agg.group_key}`}>
                    <td>{agg.portfolio_id}</td>
                    <td>{agg.group_key}</td>
                    <td>{agg.net_delta.toFixed(2)}</td>
                    <td>{agg.net_gamma.toFixed(2)}</td>
                    <td>{agg.net_vega.toFixed(2)}</td>
                    <td>{agg.net_theta.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
      </AsyncBlock>
    </section>
  );
}
