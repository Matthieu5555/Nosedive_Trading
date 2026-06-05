import { useState } from "react";

import type { SurfaceResponse } from "../api";
import { AsyncBlock } from "../components/AsyncBlock";
import { useFetch } from "../hooks/useFetch";

export function SurfacesPage() {
  const [underlying, setUnderlying] = useState("AAPL");
  const state = useFetch<SurfaceResponse>(
    `/api/surfaces?underlying=${encodeURIComponent(underlying)}`,
  );

  return (
    <section>
      <h1>Volatility Surfaces</h1>
      <label>
        Underlying{" "}
        <input
          aria-label="underlying"
          value={underlying}
          onChange={(event) => setUnderlying(event.target.value.toUpperCase())}
        />
      </label>
      <AsyncBlock state={state}>
        {(data) =>
          data.n_slices === 0 ? (
            <p>No fitted surface for {data.underlying} yet. Launch a run first.</p>
          ) : (
            <table>
              <caption>
                {data.underlying} — {data.n_slices} fitted maturities
              </caption>
              <thead>
                <tr>
                  <th>Maturity (y)</th>
                  <th>a</th>
                  <th>b</th>
                  <th>ρ</th>
                  <th>m</th>
                  <th>σ</th>
                  <th>RMSE</th>
                  <th>arb-free</th>
                </tr>
              </thead>
              <tbody>
                {data.slices.map((slice) => (
                  <tr key={slice.maturity_years}>
                    <td>{slice.maturity_years.toFixed(3)}</td>
                    <td>{slice.svi_a.toFixed(4)}</td>
                    <td>{slice.svi_b.toFixed(4)}</td>
                    <td>{slice.svi_rho.toFixed(3)}</td>
                    <td>{slice.svi_m.toFixed(3)}</td>
                    <td>{slice.svi_sigma.toFixed(4)}</td>
                    <td>{slice.diagnostics.rmse.toExponential(2)}</td>
                    <td>{slice.diagnostics.arb_free ? "✓" : "✗"}</td>
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
