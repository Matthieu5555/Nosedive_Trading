import { useMemo, useState } from "react";

import {
  type AnalyticsMaturity,
  type AnalyticsSides,
  type SurfaceSide,
  TENOR_GRID,
} from "../../api";
import { atmIv, ivAtDelta, RR_DELTA } from "../../lib/scorecards";
import { tourAnchor } from "../../lib/tour";
import { GreekCurve, SmileChart, type SurfaceIdentityProps } from "../charts";
import { DollarGreeksByMaturity } from "../DollarGreeksByMaturity";
import { Grid, Stack } from "../layout";
import { PriceStructure } from "../PriceStructure";
import { RateDiagnosticsPanel } from "../RateDiagnostics";

// The 25Δ butterfly (curvature) for one slice, in vol points: IV(25Δp) + IV(25Δc) − 2·ATM. It lives
// beside the smile, where curvature is the natural read (blueprint §3.2: niveau/pente/courbure
// résument le smile). Null where the wings don't bracket.
function ConvexityReadout({ maturity }: { maturity: AnalyticsMaturity }) {
  const atm = atmIv(maturity);
  const ivPut = ivAtDelta(maturity, -RR_DELTA);
  const ivCall = ivAtDelta(maturity, RR_DELTA);
  const convexity =
    atm !== null && ivPut !== null && ivCall !== null ? ivPut + ivCall - 2 * atm : null;
  const value =
    convexity === null
      ? "-"
      : `${convexity * 100 > 0 ? "+" : ""}${(convexity * 100).toFixed(1)} vp`;
  return (
    <p className="smile-convexity" aria-label="Convexity 25Δ">
      <span className="smile-convexity__label">Convexity 25Δ</span>
      <span className="smile-convexity__value">{value}</span>
      <span className="smile-convexity__hint">
        butterfly: IV(25Δp) + IV(25Δc) − 2·ATM (vp = vol point = 0.01 IV)
      </span>
    </p>
  );
}

// The reference tenor the page opens on (the blueprint signal tenor). When 3m wasn't captured the
// selector still opens on it and shows the projection gap, so the default is honest rather than
// silently jumping to whatever tenor happens to exist.
const DEFAULT_TENOR = "3m";

// The per-tenor reading workspace, below the Volatility surface. ONE tenor selector (the pinned
// `tenor_grid`, near → far; a grid tenor the capture didn't reach is offered but resolves to a
// labelled "not captured" gap, blueprint §4.5) drives FOUR separate, aerated page elements, in this
// order top to bottom: the Smile & Greeks (the smile curve beside the Dollar Greeks numbers for the
// same tenor), the Price structure order book, the Greek-vs-strike shape chart, and the Rate
// diagnostics. The old single "Smile & Greeks" card that crammed all of these into one box is gone;
// each is its own panel so a PM reads them with room to breathe, not stacked inside one surface.
export function TenorWorkspace({
  maturities,
  currency,
  subject,
  asOf,
  closeInstant,
  mode,
  coverage,
  side = "combined",
  sides,
  sidesAvailable = ["combined"],
  perSideServed = false,
}: {
  maturities: AnalyticsMaturity[];
  currency: string;
  // The selected surface side (combined / call / put). The smile/Greeks curves already carry both
  // wings of the side's own maturities, so they need no further filter here.
  side?: SurfaceSide;
  // The per-side captured maturities, threaded through to the Price-structure order book and the
  // Dollar Greeks table so their own Combined / Calls / Puts toggles read the real per-side capture.
  sides?: AnalyticsSides;
  sidesAvailable?: SurfaceSide[];
  perSideServed?: boolean;
} & SurfaceIdentityProps) {
  // Which grid tenors actually have a captured maturity, by tenor_label.
  const capturedByTenor = useMemo(() => {
    const map = new Map<string, AnalyticsMaturity>();
    for (const m of maturities) {
      if (m.tenor_label && !map.has(m.tenor_label)) map.set(m.tenor_label, m);
    }
    return map;
  }, [maturities]);

  const [tenor, setTenor] = useState(DEFAULT_TENOR);
  const selected = capturedByTenor.get(tenor) ?? null;

  // The one tenor selector, rendered in the Smile & Greeks heading; every panel below reads the same
  // `selected` maturity from it.
  const tenorSelect = (
    <label className="selector-field">
      <span className="visually-hidden">Tenor</span>
      <select aria-label="Tenor" value={tenor} onChange={(event) => setTenor(event.target.value)}>
        {TENOR_GRID.map((label) => (
          <option key={label} value={label}>
            {label}
            {capturedByTenor.has(label) ? "" : " (not captured)"}
          </option>
        ))}
      </select>
    </label>
  );

  // A grid tenor beyond the captured span: a labelled projection gap, never a blank or a fabricated
  // curve. The same note is shown in each panel so every element honestly says why it is empty.
  const gap = (
    <p className="projection-gap" role="status">
      {tenor} is not captured for this close, nothing to show for this tenor (projection gap).
    </p>
  );

  return (
    <>
      <article
        className="panel tenor-panel"
        aria-label="Smile and Greeks"
        {...tourAnchor(
          "market.smile",
          "Smile and Greeks",
          "The smile, implied vol across strikes, with the option Greeks beside it.",
        )}
      >
        <Stack gap="md">
          <div className="panel-heading">
            <div>
              <p className="panel-kicker">tenor</p>
              <h2>Smile &amp; Greeks</h2>
            </div>
            {tenorSelect}
          </div>

          {selected === null ? (
            gap
          ) : (
            <Grid min="420px" gap="lg">
              <Stack className="tenor-panel__smile" gap="2xs">
                <SmileChart
                  maturities={maturities}
                  maturityLabel={selected.label}
                  subject={subject}
                  asOf={asOf}
                  closeInstant={closeInstant}
                  mode={mode}
                  coverage={coverage}
                  side={side}
                />
                <ConvexityReadout maturity={selected} />
              </Stack>
              <DollarGreeksByMaturity
                maturities={maturities}
                maturityLabel={selected.label}
                currency={currency}
                sides={sides}
                sidesAvailable={sidesAvailable}
                perSideServed={perSideServed}
              />
            </Grid>
          )}
        </Stack>
      </article>

      <article
        className="panel"
        aria-label="Price book"
        {...tourAnchor(
          "market.price-book",
          "Price book",
          "The order book by strike, bid, ask, spread, volume and the option price.",
        )}
      >
        {selected === null ? (
          gap
        ) : (
          <PriceStructure
            maturities={maturities}
            maturityLabel={selected.label}
            currency={currency}
            sides={sides}
            sidesAvailable={sidesAvailable}
            perSideServed={perSideServed}
          />
        )}
      </article>

      <article
        className="panel"
        aria-label="Greek charts"
        {...tourAnchor(
          "market.greek-charts",
          "Greek charts",
          "How each Greek moves across strikes, delta, gamma, vega and theta as a curve.",
        )}
      >
        {selected === null ? (
          gap
        ) : (
          <GreekCurve
            maturities={maturities}
            maturityLabel={selected.label}
            subject={subject}
            asOf={asOf}
            closeInstant={closeInstant}
            mode={mode}
            coverage={coverage}
            currency={currency}
          />
        )}
      </article>

      <article
        className="panel"
        aria-label="Rate diagnostics panel"
        {...tourAnchor(
          "market.rate-diagnostics",
          "Rate diagnostics",
          "The forward, the interest rate and the implied carry/dividend behind this tenor.",
        )}
      >
        {selected === null ? (
          gap
        ) : (
          <RateDiagnosticsPanel
            diagnostics={selected.rate_diagnostics}
            maturityLabel={selected.label}
            currency={currency}
          />
        )}
      </article>
    </>
  );
}
