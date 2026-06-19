import { useMemo, useState } from "react";

import {
  type AnalyticsMaturity,
  type AnalyticsSides,
  type SurfaceSide,
  TENOR_GRID,
} from "../../api";
import { tourAnchor } from "../../lib/tour";
import { type SurfaceIdentityProps } from "../charts";
import { DollarGreeksByMaturity } from "../DollarGreeksByMaturity";
import { PriceStructure } from "../PriceStructure";
import { RateDiagnosticsPanel } from "../RateDiagnostics";
import { ChartStudio } from "./ChartStudio";

// The reference tenor the page opens on (the blueprint signal tenor). When 3m wasn't captured the
// selector still opens on it and shows the projection gap, so the default is honest rather than
// silently jumping to whatever tenor happens to exist.
const DEFAULT_TENOR = "3m";

// The per-tenor reading workspace, below the Volatility surface. ONE tenor selector (the pinned
// `tenor_grid`, near → far; a grid tenor the capture didn't reach is offered but resolves to a
// labelled "not captured" gap, blueprint §4.5) lives in the Charting studio heading and drives FOUR
// separate, aerated page elements, in this order top to bottom: the Charting studio (one chart,
// switchable between the smile and the first-/second-order Greek-vs-strike curves), the Dollar Greeks
// numbers for the same tenor, the Price structure order book, and the Rate diagnostics. The smile and
// the Greek-vs-strike chart used to be two separate panels (and the smile shared a box with the Dollar
// Greeks); they are folded into the one studio, and the Dollar Greeks are now their own element below
// it, so a PM reads each with room to breathe.
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

  // The one tenor selector, rendered in the Charting studio heading; every panel below reads the same
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
      <ChartStudio
        selected={selected}
        maturities={maturities}
        currency={currency}
        subject={subject}
        asOf={asOf}
        closeInstant={closeInstant}
        mode={mode}
        coverage={coverage}
        side={side}
        sidesAvailable={sidesAvailable}
        perSideServed={perSideServed}
        tenorControl={tenorSelect}
        gap={gap}
      />

      <article
        className="panel"
        aria-label="Dollar Greeks"
        {...tourAnchor(
          "market.dollar-greeks",
          "Dollar Greeks",
          "The option Greeks for this tenor as numbers, raw and in currency, by delta band.",
        )}
      >
        {selected === null ? (
          gap
        ) : (
          <DollarGreeksByMaturity
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
