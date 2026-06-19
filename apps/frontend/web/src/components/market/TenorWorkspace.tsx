import { useMemo, useState } from "react";

import { type AnalyticsMaturity, type AnalyticsSides, type SurfaceSide } from "../../api";
import { tourAnchor } from "../../lib/tour";
import { type SurfaceIdentityProps } from "../charts";
import { DollarGreeksByMaturity } from "../DollarGreeksByMaturity";
import { PriceStructure } from "../PriceStructure";
import { RateDiagnosticsPanel } from "../RateDiagnostics";
import { ChartStudio } from "./ChartStudio";

// The reference maturity the page opens on (the blueprint signal tenor, ~3 months). We open on the
// captured maturity nearest this, so the default is a real expiry that exists rather than a fixed
// label that may not have been captured.
const REFERENCE_YEARS = 0.25;

// The per-maturity reading workspace, below the Volatility surface. ONE maturity selector lists every
// captured maturity for this close, near → far, each labelled by its real listed expiry. It lives in
// the Charting studio heading and drives FOUR separate, aerated page elements, in this order top to
// bottom: the Charting studio (one chart,
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
  // Every captured maturity for this close, near → far. The selector lists exactly these, so a PM
  // only ever picks a maturity that was really captured (keyed by its listed expiry, the unique
  // `tenor_label`).
  const ordered = useMemo(
    () => [...maturities].sort((a, b) => a.maturity_years - b.maturity_years),
    [maturities],
  );

  // Open on the captured maturity nearest the reference tenor (~3m). When the active maturity set
  // changes (a new underlying or close) and the held pick is gone, fall back to that same default
  // rather than leaving the workspace blank.
  const defaultTenorLabel = useMemo(() => {
    if (ordered.length === 0) return null;
    let best = ordered[0];
    for (const m of ordered) {
      if (
        Math.abs(m.maturity_years - REFERENCE_YEARS) <
        Math.abs(best.maturity_years - REFERENCE_YEARS)
      ) {
        best = m;
      }
    }
    return best.tenor_label;
  }, [ordered]);

  const [picked, setPicked] = useState<string | null>(null);
  const selected =
    ordered.find((m) => m.tenor_label === picked) ??
    ordered.find((m) => m.tenor_label === defaultTenorLabel) ??
    null;

  // The one maturity selector, rendered in the Charting studio heading; every panel below reads the
  // same `selected` maturity from it.
  const tenorSelect = (
    <label className="selector-field">
      <span className="visually-hidden">Maturity</span>
      <select
        aria-label="Maturity"
        value={selected?.tenor_label ?? ""}
        onChange={(event) => setPicked(event.target.value)}
      >
        {ordered.map((m) => (
          <option key={m.tenor_label} value={m.tenor_label}>
            {m.label}
          </option>
        ))}
      </select>
    </label>
  );

  // No captured maturity for this close: a labelled gap, never a blank or a fabricated curve. The
  // same note is shown in each panel so every element honestly says why it is empty.
  const gap = (
    <p className="projection-gap" role="status">
      No maturities were captured for this close, nothing to show (projection gap).
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
