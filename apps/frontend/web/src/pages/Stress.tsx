// The Phase-2 stress/scenario page (WS 2B): the basket's PnL as a 3D Plotly surface over the
// ±spot × ±vol shock grid, read back from GET /api/risk/scenarios (the cron writes the
// full-reprice cells; the BFF only reshapes — ADR 0006/0034). Every cell is a full reprice,
// so the surface diverges from a local Greek approximation at large shocks by design. The
// panel self-labels its shock conventions and the PnL unit; an absent surface is a labelled
// empty state, a fetch error renders through AsyncBlock (never a blank page).

import type { Data } from "plotly.js";

import { AsyncBlock } from "../components/AsyncBlock";
import { Plot } from "../components/Plot";
import { useFetch } from "../hooks/useFetch";
import type { ScenariosResponse } from "../stressApi";

const SURFACE_LABEL =
  "Stress PnL surface — full reprice over spot shock (relative) × vol shock (additive)";

export function StressPage() {
  const state = useFetch<ScenariosResponse>("/api/risk/scenarios");

  return (
    <section>
      <h1>Stress &amp; Scenario</h1>
      <AsyncBlock state={state}>
        {(data) => {
          const surface = data.surface;
          if (surface.n_cells === 0) {
            return <p>No stress surface persisted yet.</p>;
          }
          // Plotly surface: z is spot-major, so z[i][j] is the PnL at spot_shock[i] (the y
          // axis) and vol_shock[j] (the x axis), matching the BFF z-grid orientation.
          const trace: Data = {
            type: "surface",
            x: surface.vol_shock,
            y: surface.spot_shock,
            z: surface.scenario_pnl,
            name: "stress PnL",
          };
          return (
            <>
              <p>
                Spot shock is relative (new spot = spot × (1 + s)); vol shock is additive (new
                vol = vol + v). The centre cell (0, 0) is ≈ 0 PnL by construction.
              </p>
              <p>
                PnL unit: <strong>{surface.unit}</strong>
                {surface.scenario_version ? ` · version ${surface.scenario_version}` : ""}
              </p>
              <Plot
                label={SURFACE_LABEL}
                data={[trace]}
                layout={{
                  scene: {
                    xaxis: { title: { text: "vol shock (additive, vol pts)" } },
                    yaxis: { title: { text: "spot shock (relative)" } },
                    zaxis: { title: { text: `PnL (${surface.unit})` } },
                  },
                }}
              />
            </>
          );
        }}
      </AsyncBlock>
    </section>
  );
}
